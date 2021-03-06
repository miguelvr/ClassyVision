#!/usr/bin/env python3
# Copyright (c) Facebook, Inc. and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

from enum import Enum, auto
from typing import Any, Dict, Sequence

from . import (
    ClassyParamScheduler,
    UpdateInterval,
    build_param_scheduler,
    register_param_scheduler,
)


@register_param_scheduler("composite")
class CompositeParamScheduler(ClassyParamScheduler):
    """
    Composite parameter scheduler composed of intermediate schedulers.
    Takes a list of schedulers and a list of lengths corresponding to
    percentage of training each scheduler should run for. Schedulers
    are run in order. All values in lengths should sum to 1.0.

    Each scheduler also has a corresponding interval scale. If interval
    scale is 'fixed', the intermidiate scheduler will be run without any rescaling
    of the time. If interval scale is 'rescaled', intermediate scheduler is
    run such that each scheduler will start and end at the same values as it
    would if it were the only scheduler. Default is 'rescaled' for all schedulers.

    Example:

        .. code-block:: python

              update_interval = "step"
              schedulers = [
                {"name": "constant", "value": 0.42},
                {"name": "cosine_decay", "start_lr": 0.42, "end_lr": 0.0001}
              ]
              interval_scaling = ['rescaled', 'rescaled'],
              lengths =  [0.3, 0.7]

    The parameter value will be 0.42 for the first [0%, 30%) of steps,
    and then will cosine decay from 0.42 to 0.0001 for [30%, 100%) of
    training.
    """

    class IntervalScaling(Enum):
        RESCALED = auto()
        FIXED = auto()

    def __init__(
        self,
        schedulers: Sequence[ClassyParamScheduler],
        lengths: Sequence[float],
        update_interval: UpdateInterval,
        interval_scaling: Sequence[IntervalScaling],
    ):
        super().__init__()
        self.update_interval = update_interval
        self._lengths = lengths
        self._schedulers = schedulers
        self._interval_scaling = interval_scaling

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "CompositeParamScheduler":
        """Instantiates a CompositeParamScheduler from a configuration.

        Args:
            config: A configuration for a CompositeParamScheduler.
                See :func:`__init__` for parameters expected in the config.

        Returns:
            A CompositeParamScheduler instance.
        """
        assert (
            "schedulers" in config and "lengths" in config
        ), "Composite scheduler needs both a list of schedulers and lengths"
        assert len(config["schedulers"]) == len(
            config["lengths"]
        ), "Schedulers and lengths must be same length"
        assert (
            len(config["schedulers"]) > 0
        ), "There must be at least one scheduler in the composite scheduler"
        assert (
            abs(sum(config["lengths"]) - 1.0) < 1e-3
        ), "The sum of all values in lengths must be 1"
        if sum(config["lengths"]) != 1.0:
            config["lengths"][-1] = 1.0 - sum(config["lengths"][:-1])
        update_interval = UpdateInterval.STEP
        if "update_interval" in config:
            assert config["update_interval"] in {
                "step",
                "epoch",
            }, "Choices for update interval are 'step' or 'epoch'"
            update_interval = UpdateInterval[config["update_interval"].upper()]
        interval_scaling = []
        if "interval_scaling" in config:
            assert len(config["schedulers"]) == len(
                config["interval_scaling"]
            ), "Schedulers and interval scaling must be the same length"
            for interval_scale in config["interval_scaling"]:
                assert interval_scale in {
                    "fixed",
                    "rescaled",
                }, "Choices for interval scaline are 'fixed' or 'rescaled'"
                interval_scaling.append(cls.IntervalScaling[interval_scale.upper()])
        else:
            interval_scaling = [cls.IntervalScaling.RESCALED] * len(
                config["schedulers"]
            )
        if "num_epochs" in config:  # Propogate value to intermediate schedulers
            config["schedulers"] = [
                dict(schedule, **{"num_epochs": config["num_epochs"]})
                for schedule in config["schedulers"]
            ]
        return cls(
            schedulers=[
                build_param_scheduler(scheduler) for scheduler in config["schedulers"]
            ],
            lengths=config["lengths"],
            update_interval=update_interval,
            interval_scaling=interval_scaling,
        )

    def __call__(self, where: float):
        # Find scheduler corresponding to where
        i = 0
        running_total = self._lengths[i]
        while (where + self.WHERE_EPSILON) > running_total and i < len(
            self._schedulers
        ) - 1:
            i += 1
            running_total += self._lengths[i]
        scheduler = self._schedulers[i]
        scheduler_where = where
        interval_scale = self._interval_scaling[i]
        if interval_scale == self.IntervalScaling.RESCALED:
            # Calculate corresponding where % for scheduler
            scheduler_start = running_total - self._lengths[i]
            scheduler_where = (where - scheduler_start) / self._lengths[i]
        return scheduler(scheduler_where)
