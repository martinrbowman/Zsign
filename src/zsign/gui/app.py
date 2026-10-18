from __future__ import annotations

import os
import sys

from PySide6.QtWidgets import QApplication

from zsign.gui.controller import VaultController
from zsign.gui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    # ZSIGN_IMGTOOL pins the exact imgtool matching your MCUboot tree
    # (e.g. .../bootloader/mcuboot/scripts/imgtool.py), per spec.md's
    # pinned-imgtool requirement. Unset falls back to PATH lookup.
    controller = VaultController(imgtool_executable=os.environ.get("ZSIGN_IMGTOOL") or None)
    window = MainWindow(controller=controller)
    window.resize(720, 480)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
