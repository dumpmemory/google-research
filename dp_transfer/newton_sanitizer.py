# coding=utf-8
# Copyright 2025 The Google Research Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Newton step DP sanitizer."""

import functools

import jax
import jax.numpy as jnp
import numpy as np


def clip(x, clip_norm=1.0):
  divisor = jnp.maximum(jnp.linalg.norm(x) / clip_norm, 1.)
  return x / divisor


def noisy(x, s, key):
  if 0 < s < np.inf:
    key, subkey = jax.random.split(key)
    noise = jax.random.normal(subkey, shape=jnp.shape(x)) * s
    return x + noise
  return x


class Sanitizer(object):
  """Provides a set of functions for sanitizing private information.

  There are utilities used during pre-processing of the data, and others used
  during training. Sanitizer also provides a compute_epsilon function to compute
  the privacy loss for its sanitization process.

  Training:
    clip: clips the norm of each user embedding. Used during row solves.
    apply_noise: adds noise to a list of the input sufficient statistics
      involving the user data. Used during column solves.
  """

  def __init__(self,
               steps,
               max_norm=1,
               num_classes=1000,
               s1=0,
               s2=0,
               random_seed=None):
    """Initializes a Sanitizer.

    Args:
      steps: number of optimization steps.
      max_norm: clips the user embeddings to max_norm.
      num_classes: number of classifier classes.
      s1: the noise factor for local gramian.
      s2: the noise factor for rhs.
      random_seed: rng seed for sampling.
    """
    if random_seed:
      self.key = jax.random.PRNGKey(random_seed)
    else:
      self.key = jax.random.PRNGKey(42)
    self.max_norm = max_norm
    self.sigmas = [s1, s2]
    self.steps = steps
    self.num_classes = num_classes

  def refresh_key(self):
    """Use PRNG key only once, this function refreshes it once its used."""
    _, self.key = jax.random.split(self.key)

  def clip(self, embeddings):
    if not self.max_norm:
      return embeddings
    return jax.vmap(functools.partial(clip, clip_norm=self.max_norm))(
        embeddings)

  def _project_psd(self, x, rank):
    """Project a rank 2 or 3 tensor to PSD."""
    if rank == 2:
      indices = [1, 0]
    elif rank == 3:
      indices = [0, 2, 1]
    else:
      raise ValueError("rank must be 2 or 3")

    def transpose(x):
      return jnp.transpose(x, indices)

    x = (x + transpose(x)) / 2
    e, v = jnp.linalg.eigh(x)
    e = jnp.maximum(e, 0)
    return v @ (jnp.expand_dims(e, -1) * transpose(v))

  def apply_noise(self, stats):
    """Apply noise to stats."""
    if not isinstance(stats, (list, tuple)) and len(stats) != 2:
      raise ValueError("stats must a triple of (local_gramian, " "rhs).")
    if not self.max_norm:
      return stats
    sigmas = self.sigmas
    max_norm = self.max_norm
    num_classes_scale = np.sqrt(self.num_classes)
    num_classes_scale_hessian = num_classes_scale
    num_classes_scale_gradient = num_classes_scale
    sigmas = [
        num_classes_scale_hessian * sigmas[0] * (max_norm**2) / 4.0,
        num_classes_scale_gradient * sigmas[1] * max_norm
    ]
    # Get fresh keys.
    keys = jax.random.split(self.key, num=2)

    lhs, rhs = [
        noisy(x, s, key=key)
        for x, s, key in zip(stats, sigmas, keys)
    ]
    lhs = self._project_psd(lhs, rank=2)
    return lhs, rhs

  def compute_epsilon(self, target_delta):
    """Computes epsilon."""
    if not all(self.sigmas):
      return np.inf
    # The accounting is done as follows: whenever we compute a statistic with
    # L2 sensitivity k and add Gaussian noise of scale σ, the procedure is
    # (α, αβ/2)-RDP with β = k²/σ². To compose RDP processes, we sum their β.
    s1, s2 = self.sigmas
    s1_multiplier = self.steps
    s2_multiplier = self.steps
    beta = (s1_multiplier / (s1**2) + s2_multiplier / (s2**2))
    # We translate (α, αβ/2)-RDP to (ε, δ)-DP with ε = αβ/2 + log(1/δ)/(α−1).
    # We pick the α that minimizes ε, which is α = 1 + √(2log(1/δ)/β)
    alpha = 1.0 + np.sqrt(np.log(1.0 / target_delta) * 2.0 / beta)
    eps = alpha * beta / 2.0 + np.log(1.0 / target_delta) / (alpha - 1.0)
    return eps

  def set_sigmas(self,
                 target_epsilon,
                 target_delta,
                 sigma_ratio1=1):
    """Sets sigmas to get the target (epsilon, delta).

    Args:
      target_epsilon: the desired epsilon.
      target_delta: the desired delta.
      sigma_ratio1: the ratio sigma1/sigma2.
    """
    s_lower = 1e-6
    s_upper = 1e6

    def get_epsilon(s):
      self.sigmas = [sigma_ratio1 * s, s]
      return self.compute_epsilon(target_delta)

    eps = get_epsilon(s_lower)
    i = 0
    while np.abs(eps / target_epsilon - 1) > 0.0001:
      s = (s_lower + s_upper) / 2
      eps = get_epsilon(s)
      if eps > target_epsilon:
        s_lower = s
      else:
        s_upper = s
      i += 1
      if i > 1000:
        raise ValueError(
            f"No value of sigmas found for the desired (epsilon, delta)="
            f"={target_epsilon, target_delta}. Consider increasing stddev.")
    s1, s2 = self.sigmas
    print(
        f"Setting sigmas to [{s1:.2f}, {s2:.2f}], given target "
        f"(epsilon, delta)={target_epsilon, target_delta}")
