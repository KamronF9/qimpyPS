import qimpy as qp
import numpy as np
import torch
from ._lattice_systems import get_Rbasis


class Lattice:

    def __init__(self, *, rc,
                 system=None, modification=None,
                 a=None, b=None, c=None,
                 alpha=None, beta=None, gamma=None,
                 vector1=None, vector2=None, vector3=None,
                 scale=None):
        self.rc = rc
        qp.log.info('\n--- Initializing Lattice ---')

        # Get unscaled lattice vectors:
        if system:
            self.Rbasis = get_Rbasis(system, modification, a, b, c,
                                     alpha, beta, gamma)
        else:
            # Direct specification of lattice vectors:
            def check_vectors(**kwargs):
                for key, value in kwargs.items():
                    if value is None:
                        raise KeyError(key + ' must be specified')
                    try:
                        v = np.array(value, dtype=float).reshape(3)
                    except ValueError:
                        raise ValueError(key + ' must contain 3 numbers')
            check_vectors(vector1=vector1, vector2=vector2, vector3=vector3)
            self.Rbasis = torch.tensor([vector1, vector2, vector3]).T

        # Apply scale if needed:
        if scale:
            scale = torch.tensor(scale).flatten()
            assert((len(scale) == 1) or (len(scale) == 3))
            self.Rbasis = scale[:, None] * self.Rbasis
        qp.log.info('Rbasis (real-space basis in columns):\n'
                    + qp.fmt(self.Rbasis))

        # Compute reciprocal lattice vectors:
        self.Gbasis = torch.linalg.inv(self.Rbasis.T)
        qp.log.info('Gbasis (reciprocal-space basis in columns):\n'
                    + qp.fmt(self.Gbasis))
