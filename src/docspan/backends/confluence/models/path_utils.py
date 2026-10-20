"""
Path utilities for handling relative paths.
"""

import os
from pathlib import Path
from typing import Union


def safe_relative_path(path: Union[str, Path], base_path: Union[str, Path]) -> str:
    """
    Safely get a relative path without raising ValueError for paths outside the base.
    
    This function handles cases where the target path is not a subpath of the base path,
    which would normally cause Path.relative_to() to raise a ValueError.
    
    Args:
        path: Path to convert to relative
        base_path: Base path to make relative to
        
    Returns:
        String representation of the relative path or the original path if outside base
    """
    path_obj = Path(path).resolve()
    base_path_obj = Path(base_path).resolve()
    
    try:
        # Try standard relative_to
        return str(path_obj.relative_to(base_path_obj))
    except ValueError:
        # Path is outside base directory
        # Use os.path.relpath which handles paths outside the base
        rel_path = os.path.relpath(path_obj, base_path_obj)
        return rel_path