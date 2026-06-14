"""Component renderers — draw individual pieces of a slide at a given rect.

Each public function takes (slide, ctx, rect, content, style_overrides) and
draws onto the slide. The dispatch table COMPONENT_RENDERERS at the bottom
of base.py maps component type names to their renderer functions.
"""
from .base import COMPONENT_RENDERERS, Context, render_component  # noqa: F401
