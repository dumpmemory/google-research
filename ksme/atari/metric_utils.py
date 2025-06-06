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

"""Utilities for computing the KSMe loss."""

import functools
import gin
import jax
from jax import custom_jvp
import jax.numpy as jnp


EPSILON = 1e-9


# The following two functions were borrowed from
# https://github.com/google/neural-tangents/blob/master/neural_tangents/stax.py
# as they resolve the instabilities observed when using `jnp.arccos`.
@functools.partial(custom_jvp, nondiff_argnums=(1,))
def _sqrt(x, tol=0.):
  return jnp.sqrt(jnp.maximum(x, tol))


@_sqrt.defjvp
def _sqrt_jvp(tol, primals, tangents):
  x, = primals
  x_dot, = tangents
  safe_tol = max(tol, 1e-30)
  square_root = _sqrt(x, safe_tol)
  return square_root, jnp.where(x > safe_tol, x_dot / (2 * square_root), 0.)


def l2(x, y):
  return _sqrt(jnp.sum(jnp.square(x - y)))


def cosine_similarity(x, y):
  numerator = jnp.sum(x * y)
  denominator = jnp.sqrt(jnp.sum(x**2)) * jnp.sqrt(jnp.sum(y**2))
  return numerator / (denominator + EPSILON)


def dot(x, y):
  return jnp.dot(x, y)


def cosine_distance(x, y):
  cos_similarity = cosine_similarity(x, y)
  return jnp.arctan2(_sqrt(1. - cos_similarity**2), cos_similarity)


def squarify(x):
  batch_size = x.shape[0]
  if len(x.shape) > 1:
    representation_dim = x.shape[-1]
    return jnp.reshape(jnp.tile(x, batch_size),
                       (batch_size, batch_size, representation_dim))
  return jnp.reshape(jnp.tile(x, batch_size), (batch_size, batch_size))


@gin.configurable
def representation_similarities(first_representations, second_representations,
                                distance_fn,
                                similarity_fn=dot,
                                beta=0.1,
                                explicit_kernel=True,
                                return_distance_components=False):
  """Compute similarities between representations.

  This will encode our kernel.

  Args:
    first_representations: first set of representations to use.
    second_representations: second set of representations to use.
    distance_fn: function to use for computing representation distances.
    similarity_fn: function to use for computing representation similarities.
    beta: float, weight given to cosine distance between representations.
    explicit_kernel: bool, whether to encode the kernel explicitly (with cosine
      similarity). If False, will use `0.5 * (norm_sum - beta * distances**2)`.
    return_distance_components: bool, whether to return the components used for
      computing the similarities.

  Returns:
    The similarities between representations, combining the average of the norm
    of the representations and the distance given by distance_fn.
  """
  batch_size = first_representations.shape[0]
  representation_dim = first_representations.shape[-1]
  first_squared_reps = squarify(first_representations)
  first_squared_reps = jnp.reshape(first_squared_reps,
                                   [batch_size**2, representation_dim])
  second_squared_reps = squarify(second_representations)
  second_squared_reps = jnp.transpose(second_squared_reps, axes=[1, 0, 2])
  second_squared_reps = jnp.reshape(second_squared_reps,
                                    [batch_size**2, representation_dim])
  # Although these values are only used when explicit_kernel = False, we compute
  # them if requested.
  if not explicit_kernel or return_distance_components:
    base_distances = jax.vmap(distance_fn, in_axes=(0, 0))(
        first_squared_reps, second_squared_reps)
    norm_sum = (jnp.sum(jnp.square(first_squared_reps), -1) +
                jnp.sum(jnp.square(second_squared_reps), -1))
  if explicit_kernel:
    similarities = jax.vmap(similarity_fn, in_axes=(0, 0))(
        first_squared_reps, second_squared_reps)
  else:
    similarities = 0.5 * (norm_sum - beta * (base_distances**2))
  if return_distance_components:
    return similarities, norm_sum, base_distances
  return similarities


def absolute_reward_diff(r1, r2):
  return jnp.abs(r1 - r2)


@gin.configurable
def target_similarities(representations, rewards, distance_fn,
                        similarity_fn, cumulative_gamma):
  """Target distance using the metric operator."""
  next_state_distances = representation_similarities(
      representations, representations, distance_fn,
      similarity_fn=similarity_fn)
  squared_rews = squarify(rewards)
  squared_rews_transp = jnp.transpose(squared_rews)
  squared_rews = squared_rews.reshape((squared_rews.shape[0]**2))
  squared_rews_transp = squared_rews_transp.reshape(
      (squared_rews_transp.shape[0]**2))
  reward_diffs = absolute_reward_diff(squared_rews, squared_rews_transp)
  return (
      jax.lax.stop_gradient(
          1 - 0.5 * reward_diffs + cumulative_gamma * next_state_distances))
