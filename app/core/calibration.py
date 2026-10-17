"""Multinomial calibration that always returns valid distributions.

Temperature scaling (1 param) for modest samples; vector scaling (temp + 3 biases) for larger ones.
Below `min_samples` the calibrator is the identity and says so (calibrated=False).
"""
from __future__ import annotations
import numpy as np
from scipy.optimize import minimize

MIN_SAMPLES = 300     # below this: identity, flagged uncalibrated
VECTOR_SAMPLES = 1500  # at/above this: temperature + class biases


def _softmax(z):
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


class Calibrator:
    def __init__(self, temp=1.0, bias=None, fitted=False, n=0, method="identity"):
        self.temp, self.bias = temp, np.zeros(3) if bias is None else np.asarray(bias, float)
        self.fitted, self.n, self.method = fitted, n, method

    def transform(self, P):
        P = np.clip(np.asarray(P, float), 1e-9, 1)
        if not self.fitted:
            return P / P.sum(axis=1, keepdims=True)
        return _softmax(np.log(P) / self.temp + self.bias)

    def info(self):
        return {"calibrated": self.fitted, "method": self.method, "n_fit": self.n,
                "temperature": float(self.temp), "bias": self.bias.tolist()}


def fit_calibrator(P, y, min_samples: int = MIN_SAMPLES, vector_samples: int = VECTOR_SAMPLES) -> Calibrator:
    P, y = np.clip(np.asarray(P, float), 1e-9, 1), np.asarray(y, int)
    n = len(y)
    if n < min_samples:
        return Calibrator(n=n)
    L = np.log(P)
    onehot = np.eye(3)[y]

    def nll_t(p):
        Q = _softmax(L / p[0])
        return -np.log(np.clip((Q * onehot).sum(1), 1e-12, 1)).mean()

    t = minimize(nll_t, [1.0], bounds=[(0.3, 5.0)], method="L-BFGS-B")
    if n < vector_samples:
        return Calibrator(float(t.x[0]), None, True, n, "temperature")

    def nll_v(p):
        Q = _softmax(L / p[0] + np.array([p[1], p[2], 0.0]))
        return -np.log(np.clip((Q * onehot).sum(1), 1e-12, 1)).mean() + 1e-3 * (p[1] ** 2 + p[2] ** 2)

    v = minimize(nll_v, [t.x[0], 0.0, 0.0], bounds=[(0.3, 5.0), (-1, 1), (-1, 1)], method="L-BFGS-B")
    return Calibrator(float(v.x[0]), [v.x[1], v.x[2], 0.0], True, n, "temperature+bias")
