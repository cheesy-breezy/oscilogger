import os
from pathlib import Path


RESOURCE_PLACEHOLDER = "TCPIP::0.0.0.0::INSTR"


def get_scope_resource():
    """Load the instrument address without embedding local network details in code."""
    resource = os.environ.get("OSCILLOSCOPE_RESOURCE", "").strip()
    if resource:
        return resource
    local_path = Path(__file__).resolve().with_name(".scope_resource")
    if local_path.is_file():
        return local_path.read_text(encoding="utf-8").strip()
    return RESOURCE_PLACEHOLDER


DEFAULT_RESOURCE = get_scope_resource()
