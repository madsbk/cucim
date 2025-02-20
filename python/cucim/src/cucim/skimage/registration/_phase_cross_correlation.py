"""
Port of Manuel Guizar's code from:
http://www.mathworks.com/matlabcentral/fileexchange/18401-efficient-subpixel-image-registration-by-cross-correlation
"""

import math

import cupy as cp
import numpy as np

from .._shared.fft import fftmodule as fft
from ._masked_phase_cross_correlation import _masked_phase_cross_correlation


def _upsampled_dft(data, upsampled_region_size,
                   upsample_factor=1, axis_offsets=None):
    """
    Upsampled DFT by matrix multiplication.

    This code is intended to provide the same result as if the following
    operations were performed:
        - Embed the array "data" in an array that is ``upsample_factor`` times
          larger in each dimension.  ifftshift to bring the center of the
          image to (1,1).
        - Take the FFT of the larger array.
        - Extract an ``[upsampled_region_size]`` region of the result, starting
          with the ``[axis_offsets+1]`` element.

    It achieves this result by computing the DFT in the output array without
    the need to zeropad. Much faster and memory efficient than the zero-padded
    FFT approach if ``upsampled_region_size`` is much smaller than
    ``data.size * upsample_factor``.

    Parameters
    ----------
    data : array
        The input data array (DFT of original data) to upsample.
    upsampled_region_size : integer or tuple of integers, optional
        The size of the region to be sampled.  If one integer is provided, it
        is duplicated up to the dimensionality of ``data``.
    upsample_factor : integer, optional
        The upsampling factor.  Defaults to 1.
    axis_offsets : tuple of integers, optional
        The offsets of the region to be sampled.  Defaults to None (uses
        image center)

    Returns
    -------
    output : ndarray
            The upsampled DFT of the specified region.
    """
    # if people pass in an integer, expand it to a list of equal-sized sections
    if not hasattr(upsampled_region_size, "__iter__"):
        upsampled_region_size = [upsampled_region_size] * data.ndim
    else:
        if len(upsampled_region_size) != data.ndim:
            raise ValueError("shape of upsampled region sizes must be equal "
                             "to input data's number of dimensions.")

    if axis_offsets is None:
        axis_offsets = [0] * data.ndim
    else:
        if len(axis_offsets) != data.ndim:
            raise ValueError("number of axis offsets must be equal to input "
                             "data's number of dimensions.")

    im2pi = 1j * 2 * np.pi

    dim_properties = list(zip(data.shape, upsampled_region_size, axis_offsets))

    for (n_items, ups_size, ax_offset) in dim_properties[::-1]:
        kernel = ((cp.arange(ups_size) - ax_offset)[:, None]
                  * fft.fftfreq(n_items, upsample_factor))
        kernel = cp.exp(-im2pi * kernel)
        # CuPy Backend: use kernel of same precision as the data
        kernel = kernel.astype(data.dtype, copy=False)

        # Equivalent to:
        #   data[i, j, k] = kernel[i, :] @ data[j, k].T
        data = cp.tensordot(kernel, data, axes=(1, -1))
    return data


def _compute_phasediff(cross_correlation_max):
    """
    Compute global phase difference between the two images (should be
        zero if images are non-negative).

    Parameters
    ----------
    cross_correlation_max : complex
        The complex value of the cross correlation at its maximum point.
    """
    return cp.arctan2(cross_correlation_max.imag, cross_correlation_max.real)


def _compute_error(cross_correlation_max, src_amp, target_amp):
    """
    Compute RMS error metric between ``src_image`` and ``target_image``.

    Parameters
    ----------
    cross_correlation_max : complex
        The complex value of the cross correlation at its maximum point.
    src_amp : float
        The normalized average image intensity of the source image
    target_amp : float
        The normalized average image intensity of the target image
    """
    error = 1.0 - cross_correlation_max * cross_correlation_max.conj() /\
        (src_amp * target_amp)

    return cp.sqrt(cp.abs(error))


def phase_cross_correlation(reference_image, moving_image, *,
                            upsample_factor=1, space="real",
                            return_error=True, reference_mask=None,
                            moving_mask=None, overlap_ratio=0.3):
    """Efficient subpixel image translation registration by cross-correlation.

    This code gives the same precision as the FFT upsampled cross-correlation
    in a fraction of the computation time and with reduced memory requirements.
    It obtains an initial estimate of the cross-correlation peak by an FFT and
    then refines the shift estimation by upsampling the DFT only in a small
    neighborhood of that estimate by means of a matrix-multiply DFT.

    Parameters
    ----------
    reference_image : array
        Reference image.
    moving_image : array
        Image to register. Must be same dimensionality as
        ``reference_image``.
    upsample_factor : int, optional
        Upsampling factor. Images will be registered to within
        ``1 / upsample_factor`` of a pixel. For example
        ``upsample_factor == 20`` means the images will be registered
        within 1/20th of a pixel. Default is 1 (no upsampling).
        Not used if any of ``reference_mask`` or ``moving_mask`` is not None.
    space : string, one of "real" or "fourier", optional
        Defines how the algorithm interprets input data. "real" means
        data will be FFT'd to compute the correlation, while "fourier"
        data will bypass FFT of input data. Case insensitive. Not
        used if any of ``reference_mask`` or ``moving_mask`` is not
        None.
    return_error : bool, optional
        Returns error and phase difference if on, otherwise only
        shifts are returned. Has noeffect if any of ``reference_mask`` or
        ``moving_mask`` is not None. In this case only shifts is returned.
    reference_mask : ndarray
        Boolean mask for ``reference_image``. The mask should evaluate
        to ``True`` (or 1) on valid pixels. ``reference_mask`` should
        have the same shape as ``reference_image``.
    moving_mask : ndarray or None, optional
        Boolean mask for ``moving_image``. The mask should evaluate to ``True``
        (or 1) on valid pixels. ``moving_mask`` should have the same shape
        as ``moving_image``. If ``None``, ``reference_mask`` will be used.
    overlap_ratio : float, optional
        Minimum allowed overlap ratio between images. The correlation for
        translations corresponding with an overlap ratio lower than this
        threshold will be ignored. A lower `overlap_ratio` leads to smaller
        maximum translation, while a higher `overlap_ratio` leads to greater
        robustness against spurious matches due to small overlap between
        masked images. Used only if one of ``reference_mask`` or
        ``moving_mask`` is None.

    Returns
    -------
    shifts : ndarray
        Shift vector (in pixels) required to register ``moving_image``
        with ``reference_image``. Axis ordering is consistent with
        numpy (e.g. Z, Y, X)
    error : float
        Translation invariant normalized RMS error between
        ``reference_image`` and ``moving_image``.
    phasediff : float
        Global phase difference between the two images (should be
        zero if images are non-negative).

    References
    ----------
    .. [1] Manuel Guizar-Sicairos, Samuel T. Thurman, and James R. Fienup,
           "Efficient subpixel image registration algorithms,"
           Optics Letters 33, 156-158 (2008). :DOI:`10.1364/OL.33.000156`
    .. [2] James R. Fienup, "Invariant error metrics for image reconstruction"
           Optics Letters 36, 8352-8357 (1997). :DOI:`10.1364/AO.36.008352`
    .. [3] Dirk Padfield. Masked Object Registration in the Fourier Domain.
           IEEE Transactions on Image Processing, vol. 21(5),
           pp. 2706-2718 (2012). :DOI:`10.1109/TIP.2011.2181402`
    .. [4] D. Padfield. "Masked FFT registration". In Proc. Computer Vision and
           Pattern Recognition, pp. 2918-2925 (2010).
           :DOI:`10.1109/CVPR.2010.5540032`
    """
    if (reference_mask is not None) or (moving_mask is not None):
        return _masked_phase_cross_correlation(reference_image, moving_image,
                                               reference_mask, moving_mask,
                                               overlap_ratio)

    # images must be the same shape
    if reference_image.shape != moving_image.shape:
        raise ValueError("images must be same shape")

    # assume complex data is already in Fourier space
    if space.lower() == 'fourier':
        src_freq = reference_image
        target_freq = moving_image
    # real data needs to be fft'd.
    elif space.lower() == 'real':
        src_freq = fft.fftn(reference_image)
        target_freq = fft.fftn(moving_image)
    else:
        raise ValueError('space argument must be "real" of "fourier"')

    # Whole-pixel shift - Compute cross-correlation by an IFFT
    shape = src_freq.shape
    image_product = src_freq * target_freq.conj()
    cross_correlation = fft.ifftn(image_product)

    # Locate maximum
    maxima = cp.unravel_index(
        cp.argmax(cp.abs(cross_correlation)), cross_correlation.shape
    )
    midpoints = cp.asarray([np.fix(axis_size / 2) for axis_size in shape])

    float_dtype = image_product.real.dtype
    shifts = cp.stack([m.astype(float_dtype, copy=False) for m in maxima])
    shifts[shifts > midpoints] -= cp.asarray(shape)[shifts > midpoints]

    if upsample_factor == 1:
        if return_error:
            sabs = cp.abs(src_freq)
            sabs *= sabs
            tabs = cp.abs(target_freq)
            tabs *= tabs
            src_amp = np.sum(sabs) / src_freq.size
            target_amp = np.sum(tabs) / target_freq.size
            CCmax = cross_correlation[maxima]
    # If upsampling > 1, then refine estimate with matrix multiply DFT
    else:
        # Initial shift estimate in upsampled grid
        shifts = cp.around(shifts * upsample_factor) / upsample_factor
        upsampled_region_size = math.ceil(upsample_factor * 1.5)
        # Center of output array at dftshift + 1
        dftshift = np.fix(upsampled_region_size / 2.0)
        upsample_factor = float(upsample_factor)
        # Matrix multiply DFT around the current shift estimate
        sample_region_offset = dftshift - shifts * upsample_factor
        cross_correlation = _upsampled_dft(image_product.conj(),
                                           upsampled_region_size,
                                           upsample_factor,
                                           sample_region_offset).conj()

        # Locate maximum and map back to original pixel grid
        maxima = cp.unravel_index(cp.argmax(cp.abs(cross_correlation)),
                                  cross_correlation.shape)
        CCmax = cross_correlation[maxima]

        maxima = (
            cp.stack([m.astype(float_dtype, copy=False) for m in maxima])
            - dftshift
        )

        shifts = shifts + maxima / upsample_factor

        if return_error:
            src_amp = cp.abs(src_freq)
            src_amp *= src_amp
            src_amp = cp.sum(src_amp)
            target_amp = cp.abs(target_freq)
            target_amp *= target_amp
            target_amp = cp.sum(target_amp)

    # If its only one row or column the shift along that dimension has no
    # effect. We set to zero.
    for dim in range(src_freq.ndim):
        if shape[dim] == 1:
            shifts[dim] = 0

    if return_error:
        # Redirect user to masked_phase_cross_correlation if NaNs are observed
        if cp.isnan(CCmax) or cp.isnan(src_amp) or cp.isnan(target_amp):
            raise ValueError(
                "NaN values found, please remove NaNs from your "
                "input data or use the `reference_mask`/`moving_mask` "
                "keywords, eg: "
                "phase_cross_correlation(reference_image, moving_image, "
                "reference_mask=~np.isnan(reference_image), "
                "moving_mask=~np.isnan(moving_image))")

        return shifts, _compute_error(CCmax, src_amp, target_amp),\
            _compute_phasediff(CCmax)
    else:
        return shifts
