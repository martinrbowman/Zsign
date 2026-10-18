import os

# Must be set before any PySide6/Qt import anywhere in the test session,
# so the GUI test suite runs without a real display (CI/headless).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
