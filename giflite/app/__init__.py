"""UI-agnostic application layer: controller, events, caches.

Nothing here may import a UI toolkit -- including `PIL.ImageTk`, which pulls
in tkinter. Toolkit bitmaps belong to the frontend (ARCHITECTURE.md 11.4).
"""
