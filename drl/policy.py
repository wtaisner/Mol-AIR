from typing import Optional

import torch
import torch.nn as nn

from modules.mol_air.drl.policy_dist import CategoricalDist


class CategoricalPolicy(nn.Module):
    def __init__(
        self,
        in_features: int,
        num_discrete_actions: int,
        bias: bool = True,
        device: Optional[torch.device] = None,
        dtype = None,
        temperature: float = 1.0
    ) -> None:
        super().__init__()
        
        self._layer = nn.Linear(
            in_features,
            num_discrete_actions,
            bias,
            device,
            dtype
        )
        
        self._temperature = temperature
        
    def forward(self, x: torch.Tensor) -> CategoricalDist:
        logits = self._layer(x) / self._temperature
        return CategoricalDist(logits=logits)