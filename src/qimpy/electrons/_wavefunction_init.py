import qimpy as qp
import numpy as np
import torch


def _randn(x):
    'Generate a complex standard-normal Tensor using state int Tensor x'
    # Generate two uniform random numbers for each output:
    u = []
    for i_repeat in range(2):
        # Xor-shift RNG (64-bit):
        x ^= (x << 13)
        x ^= (x >> 7)
        x ^= (x << 17)
        u.append(((0.5**64) * x) + 0.5)
    # Complex normal random number using Box-Muller transform:
    return (torch.sqrt(-2.*torch.log(u[0]))  # Magnitude
            * torch.exp((2j*np.pi) * u[1]))  # Phase


def _randomize(self, seed=0, b_start=0, b_stop=None):
    '''Randomize wavefunction coefficients to bandwidth-limited
    random numbers in a reproducible way regardless of MPI configuration
    of the run. This is done by running a separate xor-shift random
    number generator with a different seed at each combination of spin,
    k, spinor and G-vector, looping only over the bands to generate
    the random number.

    Parameters
    ----------
    seed : int, default: 0
        Seed offset at each k and iG for xor-shift random number generator
    b_start : int, default: 0
        Starting band index (global index if MPI-split over bands)
    b_stop : int, default: None
        Stopping band index (global index if MPI-split over bands)
    '''
    assert(self.coeff is not None)
    basis = self.basis
    watch = qp.utils.StopWatch('Wavefunction.randomize', basis.rc)

    # Range of bands and basis to operate on:
    if self.band_division is None:
        b_start_local = b_start
        b_stop_local = (b_stop if b_stop else self.coeff.shape[2])
        n_bands_prev = b_start
        basis_start = basis.i_start
        basis_stop = basis.i_stop
        pad_index = basis.pad_index_mine
    else:
        b_start_local = max(b_start - self.band_division.i_start, 0)
        b_stop_local = (min(b_stop - self.band_division.i_start,
                            self.band_division.n_mine) if b_stop
                        else self.band_division.n_mine)
        n_bands_prev = self.band_division.i_start + b_start_local
        basis_start = 0
        basis_stop = basis.n_tot
        pad_index = basis.pad_index
    if b_start_local >= b_stop_local:
        return  # no bands to randomize on this process
    coeff_cur = self.coeff[:, :, b_start_local:b_stop_local]

    # Initialize random number state based on global coeff index and seed:
    def init_state(basis_index):
        i_spinor = torch.arange(basis.n_spinor,
                                device=self.coeff.device).view(1, 1, -1, 1)
        i_k = torch.arange(basis.kpoints.i_start, basis.kpoints.i_stop,
                           device=self.coeff.device).view(1, -1, 1, 1)
        i_spin = torch.arange(basis.n_spins,
                              device=self.coeff.device).view(-1, 1, 1, 1)
        return (1 + seed) + basis_index.view(1, 1, 1, -1) + basis.n_tot * (
            i_spinor + basis.n_spinor * (i_k + basis.kpoints.n_tot * i_spin))

    # Create random complex numbers for each band with index-based seed:
    i_basis = torch.arange(basis_start, basis_stop, device=self.coeff.device)
    x = init_state(i_basis)
    for i_discard in range(n_bands_prev + 1):
        _randn(x)  # get RNG to position appropriate for starting band
    for b_local in range(b_stop_local - b_start_local):
        coeff_cur[:, :, b_local] = _randn(x)

    # Enforce Hermitian symmetry in real case:
    if basis.real_wavefunctions:
        # Find subset of iG_z = 0 in current process range:
        sel = torch.where(torch.logical_and(basis.index_z0 >= basis_start,
                                            basis.index_z0 < basis_stop))[0]
        index_z0_local = basis.index_z0[sel] - basis_start
        # Create random complex numbers based on conjugate-index seed:
        x = init_state(basis.index_z0_conj[sel])
        for i_discard in range(n_bands_prev + 1):
            _randn(x)  # get RNG to position appropriate for starting band
        for b_local in range(b_stop_local - b_start_local):
            coeff_cur[:, :, b_local, :, index_z0_local] += _randn(x).conj()
        coeff_cur[..., index_z0_local] *= np.sqrt(0.5)  # keep variance = 1

    # Mask out inactive basis elements:
    coeff_cur[pad_index] = 0.

    # Bandwidth limit:
    ke = basis.get_ke(slice(basis_start, basis_stop))[None, :, None, None, :]
    coeff_cur *= 1. / (1. + ((4./3)*ke) ** 6)  # damp-out high-KE coefficients
    watch.stop()


def _randomize_selected(self, i_spin, i_k, i_band, seed=0):
    '''Randomize wavefunction coefficients of selected bands, indexed
    as a tuple (i_spin, i_k, i_band) of 1D index tensors with same shape.
    This is only supported for wavefunctions split over basis
    (i.e. no band_division)

    Parameters
    ----------
    i_spin : torch.Tensor[int]
    i_k : torch.Tensor[int]
    i_band : torch.Tensor[int]
    seed : int, default: 0
    '''
    assert(self.coeff is not None)
    assert(self.band_division is None)
    basis = self.basis
    n_bands = self.n_bands()
    watch = qp.utils.StopWatch('Wavefunction.randomize_selected', basis.rc)

    # Initialize random number state based on global coeff index and seed:
    def init_state(basis_index):
        i_spinor = torch.arange(basis.n_spinor,
                                device=self.coeff.device).view(1, -1, 1)
        i_k_global = (basis.kpoints.i_start + i_k).view(-1, 1, 1)
        return (1 + seed) + basis_index.view(1, 1, -1) + basis.n_tot * (
            i_spinor + basis.n_spinor * (
                i_band.view(-1, 1, 1) + n_bands * (
                    i_k_global + basis.kpoints.n_tot * i_spin.view(-1, 1, 1))))

    # Create random complex numbers for each band with index-based seed:
    i_basis = torch.arange(basis.i_start, basis.i_stop,
                           device=self.coeff.device)
    x = init_state(i_basis)
    _randn(x)  # warm-up RNG: discard one output after seed
    self.coeff[(i_spin, i_k, i_band)] = _randn(x)

    # Enforce Hermitian symmetry in real case:
    if basis.real_wavefunctions:
        # Find subset of iG_z = 0 in current process range:
        sel = torch.where(torch.logical_and(basis.index_z0 >= basis.i_start,
                                            basis.index_z0 < basis.i_stop))[0]
        index_z0_local = basis.index_z0[sel] - basis.i_start
        # Create random complex numbers based on conjugate-index seed:
        x = init_state(basis.index_z0_conj[sel])
        _randn(x)  # warm-up RNG: discard one output after seed
        self.coeff[(i_spin, i_k, i_band)][...,
                                          index_z0_local] += _randn(x).conj()

    # Mask out inactive basis elements:
    self.coeff[basis.pad_index_mine] = 0.

    # Bandwidth limit:
    ke = basis.get_ke(basis.mine)[i_k, None, :]
    self.coeff[(i_spin, i_k, i_band)] *= 1. / (1. + ((4./3)*ke) ** 6)
    watch.stop()
