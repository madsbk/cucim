import cupy as cp
import numpy as np
import pytest
from cupy.testing import assert_array_almost_equal, assert_array_equal
from numpy.testing import assert_equal
from skimage import data

from cucim.skimage import img_as_float
from cucim.skimage.feature import match_template, peak_local_max
from cucim.skimage.morphology import diamond


def test_template():
    size = 100
    # Float prefactors ensure that image range is between 0 and 1
    image = np.full((400, 400), 0.5)
    target = 0.1 * (np.tri(size) + np.tri(size)[::-1])
    target_positions = [(50, 50), (200, 200)]
    for x, y in target_positions:
        image[x:x + size, y:y + size] = target
    np.random.seed(1)
    image += 0.1 * np.random.uniform(size=(400, 400))
    image = cp.asarray(image)
    target = cp.asarray(target)

    result = match_template(image, target)
    delta = 5

    positions = peak_local_max(result, min_distance=delta)

    if len(positions) > 2:
        # Keep the two maximum peaks.
        intensities = result[tuple(positions.T)]
        i_maxsort = cp.argsort(intensities)[::-1]
        positions = positions[i_maxsort][:2]

    # Sort so that order matches `target_positions`.
    positions = positions[cp.argsort(positions[:, 0])]

    for xy_target, xy in zip(target_positions, positions):
        assert_array_almost_equal(xy, xy_target)


def test_normalization():
    """Test that `match_template` gives the correct normalization.

    Normalization gives 1 for a perfect match and -1 for an inverted-match.
    This test adds positive and negative squares to a zero-array and matches
    the array with a positive template.
    """
    n = 5
    N = 20
    ipos, jpos = (2, 3)
    ineg, jneg = (12, 11)
    image = cp.full((N, N), 0.5)
    image[ipos:ipos + n, jpos:jpos + n] = 1
    image[ineg:ineg + n, jneg:jneg + n] = 0

    # white square with a black border
    template = cp.zeros((n + 2, n + 2))
    template[1:1 + n, 1:1 + n] = 1

    result = match_template(image, template)

    # get the max and min results.
    sorted_result = cp.argsort(result.ravel())
    iflat_min = cp.asnumpy(sorted_result[0])
    iflat_max = cp.asnumpy(sorted_result[-1])
    min_result = np.unravel_index(iflat_min, result.shape)
    max_result = np.unravel_index(iflat_max, result.shape)

    # shift result by 1 because of template border
    assert np.all((np.array(min_result) + 1) == (ineg, jneg))
    assert np.all((np.array(max_result) + 1) == (ipos, jpos))

    assert cp.allclose(result.ravel()[iflat_min], -1)
    assert cp.allclose(result.ravel()[iflat_max], 1)


def test_no_nans():
    """Test that `match_template` doesn't return NaN values.

    When image values are only slightly different, floating-point errors can
    cause a subtraction inside of a square root to go negative (without an
    explicit check that was added to `match_template`).
    """
    np.random.seed(1)
    image = 0.5 + 1e-9 * np.random.normal(size=(20, 20))
    template = np.ones((6, 6))
    template[:3, :] = 0
    image = cp.asarray(image)
    template = cp.asarray(template)
    result = match_template(image, template)
    assert not cp.any(cp.isnan(result))


def test_switched_arguments():
    image = cp.ones((5, 5))
    template = cp.ones((3, 3))
    with pytest.raises(ValueError):
        match_template(template, image)


def test_pad_input():
    """Test `match_template` when `pad_input=True`.

    This test places two full templates (one with values lower than the image
    mean, the other higher) and two half templates, which are on the edges of
    the image. The two full templates should score the top (positive and
    negative) matches and the centers of the half templates should score 2nd.
    """
    # Float prefactors ensure that image range is between 0 and 1
    template = 0.5 * diamond(2)
    image = 0.5 * cp.ones((9, 19))
    mid = slice(2, 7)
    image[mid, :3] -= template[:, -3:]  # half min template centered at 0
    image[mid, 4:9] += template         # full max template centered at 6
    image[mid, -9:-4] -= template       # full min template centered at 12
    image[mid, -3:] += template[:, :3]  # half max template centered at 18

    result = match_template(image, template, pad_input=True,
                            constant_values=float(image.mean()))

    # get the max and min results.
    sorted_result = cp.argsort(result.ravel())
    i, j = cp.unravel_index(sorted_result[:2], result.shape)
    assert_array_equal(j, (12, 0))
    i, j = cp.unravel_index(sorted_result[-2:], result.shape)
    assert_array_equal(j, (18, 6))


def test_3d():
    np.random.seed(1)
    template = np.random.rand(3, 3, 3)
    image = np.zeros((12, 12, 12))

    image[3:6, 5:8, 4:7] = template

    image = cp.asarray(image)
    template = cp.asarray(template)

    result = match_template(image, template)

    assert_equal(result.shape, (10, 10, 10))
    assert_equal(
        np.unravel_index(int(result.argmax()), result.shape), (3, 5, 4)
    )


def test_3d_pad_input():
    np.random.seed(1)
    template = np.random.rand(3, 3, 3)
    image = np.zeros((12, 12, 12))

    image[3:6, 5:8, 4:7] = template

    image = cp.asarray(image)
    template = cp.asarray(template)

    result = match_template(image, template, pad_input=True)

    assert_equal(result.shape, (12, 12, 12))
    assert_equal(
        np.unravel_index(int(result.argmax()), result.shape), (4, 6, 5)
    )


def test_padding_reflect():
    template = diamond(2)
    image = cp.zeros((10, 10))
    image[2:7, :3] = template[:, -3:]

    result = match_template(image, template, pad_input=True, mode="reflect")

    assert_equal(np.unravel_index(int(result.argmax()), result.shape), (4, 0))


def test_wrong_input():
    image = cp.ones((5, 5, 1))
    template = cp.ones((3, 3))
    with pytest.raises(ValueError):
        match_template(template, image)

    image = cp.ones((5, 5))
    template = cp.ones((3, 3, 2))
    with pytest.raises(ValueError):
        match_template(template, image)

    image = cp.ones((5, 5, 3, 3))
    template = cp.ones((3, 3, 2))
    with pytest.raises(ValueError):
        match_template(template, image)


def test_bounding_values():
    image = img_as_float(cp.asarray(data.page()))
    template = cp.zeros((3, 3))
    template[1, 1] = 1
    result = match_template(image, template)
    print(result.max())
    assert result.max() < 1 + 1e-7
    assert result.min() > -1 - 1e-7
