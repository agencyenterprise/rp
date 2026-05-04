"""Tests for PodManager capacity-error suggestions."""

from unittest.mock import MagicMock

import pytest

from rp.core.models import GPUSpec, PodCreateRequest
from rp.core.pod_manager import PodManager, _looks_like_capacity_error
from rp.utils.errors import PodError


class TestLooksLikeCapacityError:
    """The phrase-matching heuristic that decides when to dress an error
    up with GPU suggestions vs. let it pass through unchanged."""

    @pytest.mark.parametrize(
        "msg",
        [
            "There are no longer any instances available with the requested specifications.",
            "no instances available",
            "We're currently no available pods of that type.",
            "Region is out of capacity",
            "GPU type is out of stock for now",
            "Not enough capacity in this datacenter",
        ],
    )
    def test_matches_capacity_phrases(self, msg):
        assert _looks_like_capacity_error(Exception(msg)) is True

    def test_uses_runpod_cli_error_details_field(self):
        """RunPodCLIError stores the runpod SDK's message under .details, not
        in str(); the heuristic must look there too."""
        err = PodError.creation_failed(
            "no longer any instances available with the requested specs"
        )
        assert _looks_like_capacity_error(err) is True

    def test_does_not_match_unrelated_errors(self):
        assert _looks_like_capacity_error(Exception("invalid api key")) is False
        assert (
            _looks_like_capacity_error(Exception("network volume not found")) is False
        )


class TestCreatePodCapacitySuggestions:
    """When every variant of the requested GPU is out of stock, the raised
    PodError should list nearby-VRAM alternatives the user can copy-paste."""

    def _gpus(self):
        return [
            {"id": "NVIDIA H200", "displayName": "H200 SXM", "memoryInGb": 141},
            {"id": "NVIDIA H100 NVL", "displayName": "H100 NVL", "memoryInGb": 94},
            {"id": "NVIDIA H100 SXM", "displayName": "H100 SXM", "memoryInGb": 80},
            {"id": "NVIDIA A100 SXM", "displayName": "A100 SXM", "memoryInGb": 80},
            {"id": "NVIDIA A100 PCIe", "displayName": "A100 PCIe", "memoryInGb": 40},
            {"id": "NVIDIA L40S", "displayName": "L40S", "memoryInGb": 48},
            {"id": "NVIDIA RTX 4090", "displayName": "RTX 4090", "memoryInGb": 24},
        ]

    def _build(self):
        api = MagicMock()
        api.find_gpu_type_ids.return_value = ["NVIDIA H200"]
        api.get_gpus.return_value = self._gpus()
        api.create_pod.side_effect = PodError.creation_failed(
            "There are no longer any instances available with the requested specifications."
        )
        pm = PodManager(api_client=api)
        return pm, api

    def test_capacity_error_lists_nearest_vram_alternatives(self):
        pm, _ = self._build()
        request = PodCreateRequest(
            alias="x", gpu_spec=GPUSpec(count=2, model="H200"), volume_gb=200
        )
        with pytest.raises(PodError) as exc_info:
            pm.create_pod(request)

        details = exc_info.value.details or ""
        # The original raw error must be preserved so users see the source.
        assert "no longer any instances available" in details
        # Should suggest at least one alternative as a copy-pasteable command,
        # using the requested 2x prefix.
        assert "rp up --gpu '2x" in details
        # The closest-VRAM alternative to H200 (141GB) is H100 NVL (94GB).
        # It must appear in the suggestion list.
        assert "H100 NVL" in details
        # The tried-and-failed type itself must NOT be re-suggested.
        assert "rp up --gpu '2xNVIDIA H200'" not in details

    def test_unrelated_error_passes_through_unchanged(self):
        """Non-capacity errors (auth, validation, etc.) must not be wrapped
        — we'd only confuse the user with irrelevant GPU suggestions."""
        api = MagicMock()
        api.find_gpu_type_ids.return_value = ["NVIDIA H200"]
        api.create_pod.side_effect = PodError.creation_failed("Authentication failed")
        pm = PodManager(api_client=api)

        request = PodCreateRequest(
            alias="x", gpu_spec=GPUSpec(count=1, model="H200"), volume_gb=200
        )
        with pytest.raises(PodError) as exc_info:
            pm.create_pod(request)
        # No alternatives prepended — original message stands alone.
        details = exc_info.value.details or ""
        assert details == "Authentication failed"
        assert "rp up --gpu" not in details

    def test_get_gpus_failure_falls_back_gracefully(self):
        """If the alternative-listing API call also fails, we shouldn't mask
        the original capacity error — surface it bare with a small hint."""
        pm, api = self._build()
        api.get_gpus.side_effect = RuntimeError("api down")

        request = PodCreateRequest(
            alias="x", gpu_spec=GPUSpec(count=1, model="H200"), volume_gb=200
        )
        with pytest.raises(PodError) as exc_info:
            pm.create_pod(request)
        details = exc_info.value.details or ""
        assert "no longer any instances available" in details
        assert "rp pod gpus" in details
