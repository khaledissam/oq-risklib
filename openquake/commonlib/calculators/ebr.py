#  -*- coding: utf-8 -*-
#  vim: tabstop=4 shiftwidth=4 softtabstop=4

#  Copyright (c) 2015, GEM Foundation

#  OpenQuake is free software: you can redistribute it and/or modify it
#  under the terms of the GNU Affero General Public License as published
#  by the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.

#  OpenQuake is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.

#  You should have received a copy of the GNU Affero General Public License
#  along with OpenQuake.  If not, see <http://www.gnu.org/licenses/>.

import logging
import operator

import numpy

from openquake.baselib.general import AccumDict, humansize
from openquake.commonlib.calculators import base
from openquake.commonlib import readinput, parallel, datastore
from openquake.risklib import riskinput
from openquake.commonlib.parallel import apply_reduce

elt_dt = numpy.dtype([('rup_id', numpy.uint32), ('loss', numpy.float32)])

OUTPUTS = ['event_loss_table-rlzs', 'insured_loss_table-rlzs',
           'rcurves-rlzs', 'insured_rcurves-rlzs']

ELT, ILT, FRC, IRC = 0, 1, 2, 3


def cube(O, L, R, factory):
    """
    :param O: the number of different outputs
    :param L: the number of loss types
    :param R: the number of realizations
    :param factory: thunk used to initialize the elements
    :returns: a numpy array of shape (O, L, R)
    """
    losses = numpy.zeros((O, L, R), object)
    for o in range(O):
        for l in range(L):
            for r in range(R):
                losses[o, l, r] = factory()
    return losses


@parallel.litetask
def ebr(riskinputs, riskmodel, rlzs_assoc, monitor):
    """
    :param riskinputs:
        a list of :class:`openquake.risklib.riskinput.RiskInput` objects
    :param riskmodel:
        a :class:`openquake.risklib.riskinput.RiskModel` instance
    :param rlzs_assoc:
        a class:`openquake.commonlib.source.RlzsAssoc` instance
    :param monitor:
        :class:`openquake.commonlib.parallel.PerformanceMonitor` instance
    :returns:
        a numpy array of shape (O, L, R); each element is a list containing
        a single array of dtype elt_dt, or an empty list
    """
    lti = riskmodel.lti  # loss type -> index
    losses = cube(
        monitor.num_outputs, len(lti), len(rlzs_assoc.realizations),
        AccumDict)
    for out_by_rlz in riskmodel.gen_outputs(riskinputs, rlzs_assoc, monitor):
        rup_slice = out_by_rlz.rup_slice
        rup_ids = list(range(rup_slice.start, rup_slice.stop))
        for out in out_by_rlz:
            l = lti[out.loss_type]
            asset_ids = [a.idx for a in out.assets]
            agg_losses = out.event_loss_per_asset.sum(axis=1)
            agg_ins_losses = out.insured_loss_per_asset.sum(axis=1)
            for rup_id, loss, ins_loss in zip(
                    rup_ids, agg_losses, agg_ins_losses):
                if loss > 0:
                    losses[ELT, l, out.hid] += {rup_id: loss}
                if ins_loss > 0:
                    losses[ILT, l, out.hid] += {rup_id: ins_loss}
            # dictionaries asset_idx -> array of C counts
            losses[FRC, l, out.hid] += dict(
                zip(asset_ids, out.counts_matrix))
            if out.insured_counts_matrix.sum():
                losses[IRC, l, out.hid] += dict(
                    zip(asset_ids, out.insured_counts_matrix))

    for idx, dic in numpy.ndenumerate(losses):
        o, l, r = idx
        if dic:
            if o in (ELT, ILT):
                losses[idx] = [numpy.array(dic.items(), elt_dt)]
            else:  # risk curves
                losses[idx] = [dic]
        else:
            losses[idx] = []
    return losses


@base.calculators.add('ebr')
class EventBasedRiskCalculator(base.RiskCalculator):
    """
    Event based PSHA calculator generating the event loss table and
    fixed ratios loss curves.
    """
    pre_calculator = 'event_based_rupture'
    core_func = ebr

    epsilon_matrix = datastore.persistent_attribute('epsilon_matrix')
    is_stochastic = True

    def pre_execute(self):
        """
        Read the precomputed ruptures (or compute them on the fly) and
        prepare some datasets in the datastore.
        """
        super(EventBasedRiskCalculator, self).pre_execute()
        if not self.riskmodel:  # there is no riskmodel, exit early
            self.execute = lambda: None
            self.post_execute = lambda result: None
            return
        oq = self.oqparam
        epsilon_sampling = oq.epsilon_sampling
        correl_model = readinput.get_correl_model(oq)
        gsims_by_col = self.rlzs_assoc.get_gsims_by_col()
        assets_by_site = self.assets_by_site
        # the following is needed!
        self.assetcol = riskinput.build_asset_collection(
            assets_by_site, oq.time_event)

        logging.info('Populating the risk inputs')
        rup_by_tag = sum(self.datastore['sescollection'], AccumDict())
        all_ruptures = [rup_by_tag[tag] for tag in sorted(rup_by_tag)]
        num_samples = min(len(all_ruptures), epsilon_sampling)
        eps_dict = riskinput.make_eps_dict(
            assets_by_site, num_samples, oq.master_seed, oq.asset_correlation)
        logging.info('Generated %d epsilons', num_samples * len(eps_dict))
        self.epsilon_matrix = numpy.array(
            [eps_dict[a['asset_ref']] for a in self.assetcol])
        self.riskinputs = list(self.riskmodel.build_inputs_from_ruptures(
            self.sitecol.complete, all_ruptures, gsims_by_col,
            oq.truncation_level, correl_model, eps_dict,
            oq.concurrent_tasks or 1))
        logging.info('Built %d risk inputs', len(self.riskinputs))

        # preparing empty datasets
        loss_types = self.riskmodel.loss_types
        self.L = len(loss_types)
        self.R = len(self.rlzs_assoc.realizations)
        self.outs = OUTPUTS
        self.datasets = {}
        self.monitor.oqparam = self.oqparam
        # ugly: attaching an attribute needed in the task function
        self.monitor.num_outputs = len(self.outs)
        # attaching two other attributes used in riskinput.gen_outputs
        self.monitor.assets_by_site = self.assets_by_site
        self.monitor.num_assets = N = self.count_assets()
        for o, out in enumerate(self.outs):
            self.datastore.hdf5.create_group(out)
            for l, loss_type in enumerate(loss_types):
                rc_dt = self.riskmodel.curve_builders[l].poes_dt
                for r, rlz in enumerate(self.rlzs_assoc.realizations):
                    key = '/%s/rlz-%03d' % (loss_type, rlz.ordinal)
                    if o in (ELT, ILT):  # loss tables
                        dset = self.datastore.create_dset(out + key, elt_dt)
                    else:  # risk curves
                        dset = self.datastore.create_dset(out + key, rc_dt, N)
                    self.datasets[o, l, r] = dset

    def execute(self):
        """
        Run the ebr calculator in parallel and aggregate the results
        """
        return apply_reduce(
            self.core_func.__func__,
            (self.riskinputs, self.riskmodel, self.rlzs_assoc, self.monitor),
            concurrent_tasks=self.oqparam.concurrent_tasks,
            agg=self.agg,
            acc=cube(self.monitor.num_outputs, self.L, self.R, list),
            weight=operator.attrgetter('weight'),
            key=operator.attrgetter('col_id'))

    def agg(self, acc, losses):
        """
        Aggregate list of arrays in longer lists.

        :param acc: accumulator array of shape (O, L, R)
        :param losses: a numpy array of shape (O, L, R)
        """
        for idx, arrays in numpy.ndenumerate(losses):
            acc[idx].extend(arrays)
        return acc

    def post_execute(self, result):
        """
        Save the event loss table in the datastore.

        :param result:
            a numpy array of shape (O, L, R) containing lists of arrays
        """
        nses = self.oqparam.ses_per_logic_tree_path
        saved = {out: 0 for out in self.outs}
        N = len(self.assetcol)
        with self.monitor('saving loss table',
                          autoflush=True, measuremem=True):
            for (o, l, r), data in numpy.ndenumerate(result):
                if not data:  # empty list
                    continue
                if o in (ELT, ILT):  # loss tables, data is a list of arrays
                    losses = numpy.concatenate(data)
                    self.datasets[o, l, r].extend(losses)
                    saved[self.outs[o]] += losses.nbytes
                else:  # risk curves, data is a list of counts dictionaries
                    cb = self.riskmodel.curve_builders[l]
                    nbytes = sum(sum(v.nbytes for v in d.values())
                                 for d in data)
                    logging.info('Got %s of data', humansize(nbytes))
                    counts_matrix = cb.get_counts(N, data)
                    curves = cb.build_flr_curves(
                        counts_matrix, nses, self.assetcol)
                    self.datasets[o, l, r].dset[:] = curves
                    saved[self.outs[o]] += curves.nbytes
                self.datastore.hdf5.flush()

        for out in self.outs:
            nbytes = saved[out]
            if nbytes:
                self.datastore[out].attrs['nbytes'] = nbytes
                logging.info('Saved %s in %s', humansize(nbytes), out)
            else:  # remove empty outputs
                del self.datastore[out]
