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

from __future__ import division
import numpy


def max_rel_diff(curve_ref, curve, min_value=0.01):
    """
    Compute the maximum relative difference between two curves. Only values
    greather or equal than the min_value are considered.

    >>> curve_ref = [0.01, 0.02, 0.03, 0.05, 1.0]
    >>> curve = [0.011, 0.021, 0.031, 0.051, 1.0]
    >>> round(max_rel_diff(curve_ref, curve), 2)
    0.1
    """
    assert len(curve_ref) == len(curve), (len(curve_ref), len(curve))
    assert len(curve), 'The curves are empty!'
    max_diff = 0
    for c1, c2 in zip(curve_ref, curve):
        if c1 >= min_value:
            max_diff = max(max_diff, abs(c1 - c2) / c1)
    return max_diff


def max_rel_diff_index(curve_ref, curve, min_value=0.01):
    """
    Compute the maximum relative difference between two sets of curves.
    Only values greather or equal than the min_value are considered.
    Return both the maximum difference and its location (array index).

    >>> curve_refs = [[0.01, 0.02, 0.03, 0.05], [0.01, 0.02, 0.04, 0.06]]
    >>> curves = [[0.011, 0.021, 0.031, 0.051], [0.012, 0.022, 0.032, 0.051]]
    >>> max_rel_diff_index(curve_refs, curves)
    (0.2, 1)
    """
    assert len(curve_ref) == len(curve), (len(curve_ref), len(curve))
    assert len(curve), 'The curves are empty!'
    diffs = [max_rel_diff(c1, c2, min_value)
             for c1, c2 in zip(curve_ref, curve)]
    maxdiff = max(diffs)
    maxindex = diffs.index(maxdiff)
    return maxdiff, maxindex


def rmsep(array_ref, array, min_value=0.01):
    """
    Root Mean Square Error Percentage for two arrays.

    :param array_ref: reference array
    :param array: another array
    :param min_value: compare only the elements larger than min_value
    :returns: the relative distance between the arrays

    >>> curve_ref = numpy.array([[0.01, 0.02, 0.03, 0.05],
    ... [0.01, 0.02, 0.04, 0.06]])
    >>> curve = numpy.array([[0.011, 0.021, 0.031, 0.051],
    ... [0.012, 0.022, 0.032, 0.051]])
    >>> round(rmsep(curve_ref, curve), 5)
    0.11292
    """
    bigvalues = array_ref > min_value
    reldiffsquare = (1. - array[bigvalues] / array_ref[bigvalues]) ** 2
    return numpy.sqrt(reldiffsquare.mean())