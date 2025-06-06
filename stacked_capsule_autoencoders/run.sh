#!/bin/bash
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


# This is a quick test that checks if everything is running ok.
set -e
set -x

source stacked_capsule_autoencoders/setup_virtualenv.sh
python -m stacked_capsule_autoencoders.train\
  --name=test\
  --max_train_steps=5\
  "$@"
