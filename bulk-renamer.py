import sys
import os
import re
import datetime
import pathlib
import shutil
from enum import Enum
from dataclasses import dataclass, field

# --- FIX: QFileSystemModel and QAction moved to QtGui in PyQt6 ---
from PyQt6.QtGui import (
    QColor, QBrush, QAction, QFileSystemModel, QKeySequence
)

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QTreeView, QTableView, QSplitter, QGroupBox, QGridLayout, 
    QLabel, QLineEdit, QComboBox, QCheckBox, QSpinBox, 
    QPushButton, QScrollArea, QHeaderView,
    QAbstractItemView, QMessageBox, QFrame
)

from PyQt6.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, QDir, QTimer, pyqtSignal, QSize
)

# ==========================================
# 1. CORE RENAMING LOGIC ENGINE
# ==========================================

class RenameConfig:
    """Holds the state of all 14 configuration groups."""
    def __init__(self):
        # 1. Regex
        self.regex_match = ""
        self.regex_replace = ""
        self.regex_simple = False
        
        # 2. Name
        self.name_mode = "Keep"  # Keep, Remove, Fixed, Reverse
        self.name_fixed = ""
        
        # 3. Replace
        self.replace_str = ""
        self.replace_with = ""
        self.replace_case = False
        
        # 4. Case
        self.case_mode = "Same" # Same, Lower, Upper, Title, Sentence
        
        # 5. Remove
        self.remove_first = 0
        self.remove_last = 0
        self.remove_from = 0
        self.remove_to = 0
        self.remove_chars = ""
        
        # 7. Add
        self.add_prefix = ""
        self.add_suffix = ""
        self.add_insert = ""
        self.add_at_pos = 0
        
        # 10. Numbering
        self.num_mode = "None" # None, Prefix, Suffix, Insert
        self.num_start = 1
        self.num_incr = 1
        self.num_pad = 0
        self.num_sep = ""
        self.num_at = 0
        
        # 11. Extension
        self.ext_mode = "Same" # Same, Lower, Upper, Remove, Fixed
        self.ext_fixed = ""

class RenamingEngine:
    """
    Static processing engine. 
    Processes rules 1 through 14 sequentially.
    """
    
    @staticmethod
    def process(original_name: str, config: RenameConfig, index: int = 0) -> str:
        p = pathlib.Path(original_name)
        stem = p.stem
        ext = p.suffix  # includes dot
        
        # --- 1. RegEx ---
        if config.regex_match:
            try:
                # If "Simple" is checked, we treat it as simple wildcard (simplified logic here)
                # Otherwise standard python re
                pattern = config.regex_match
                if config.regex_simple:
                    pattern = re.escape(pattern).replace(r"\*", ".*")
                
                # Apply to full name or just stem? BRU usually separates ext.
                # For this clone, applying to stem.
                stem = re.sub(pattern, config.regex_replace, stem)
            except re.error:
                pass # Invalid regex, ignore

        # --- 2. Name ---
        if config.name_mode == "Remove":
            stem = ""
        elif config.name_mode == "Fixed":
            stem = config.name_fixed
        elif config.name_mode == "Reverse":
            stem = stem[::-1]

        # --- 3. Replace ---
        if config.replace_str:
            if config.replace_case:
                stem = stem.replace(config.replace_str, config.replace_with)
            else:
                # Case insensitive replace
                pattern = re.compile(re.escape(config.replace_str), re.IGNORECASE)
                stem = pattern.sub(config.replace_with, stem)

        # --- 5. Remove ---
        # Logic: First n, Last n, From/To range, Specific Chars
        if config.remove_first > 0:
            stem = stem[config.remove_first:]
        if config.remove_last > 0 and len(stem) > config.remove_last:
            stem = stem[:-config.remove_last]
            
        if config.remove_from > 0 or config.remove_to > 0:
            # BRU is 1-based index usually
            start = max(0, config.remove_from - 1)
            end = config.remove_to if config.remove_to > 0 else len(stem)
            if start < len(stem):
                # Remove the slice
                stem = stem[:start] + stem[end:]
        
        if config.remove_chars:
            table = str.maketrans("", "", config.remove_chars)
            stem = stem.translate(table)

        # --- 7. Add ---
        if config.add_prefix:
            stem = config.add_prefix + stem
        if config.add_suffix:
            stem = stem + config.add_suffix
        if config.add_insert:
            pos = config.add_at_pos
            if pos >= len(stem):
                stem += config.add_insert
            else:
                stem = stem[:pos] + config.add_insert + stem[pos:]

        # --- 10. Numbering ---
        if config.num_mode != "None":
            num_val = config.num_start + (index * config.num_incr)
            num_str = str(num_val).zfill(config.num_pad)
            
            # Combine with separator
            full_num_str = f"{config.num_sep}{num_str}" if config.num_mode == "Suffix" else f"{num_str}{config.num_sep}"
            
            if config.num_mode == "Prefix":
                stem = full_num_str + stem
            elif config.num_mode == "Suffix":
                stem = stem + full_num_str
            elif config.num_mode == "Insert":
                pos = config.num_at
                stem = stem[:pos] + full_num_str + stem[pos:]

        # --- 4. Case (Applied late usually, but order varies. BRU Group 4 is early, but often applied after mod) ---
        # We will apply it here to the resulting stem
        if config.case_mode == "Lower":
            stem = stem.lower()
        elif config.case_mode == "Upper":
            stem = stem.upper()
        elif config.case_mode == "Title":
            stem = stem.title()

        # --- 11. Extension ---
        if config.ext_mode == "Lower":
            ext = ext.lower()
        elif config.ext_mode == "Upper":
            ext = ext.upper()
        elif config.ext_mode == "Remove":
            ext = ""
        elif config.ext_mode == "Fixed":
            ext = "." + config.ext_fixed if not config.ext_fixed.startswith(".") else config.ext_fixed

        return f"{stem}{ext}"

# ==========================================
# 2. DATA MODEL
# ==========================================

@dataclass
class FileItem:
    path: pathlib.Path
    original_name: str
    new_name: str
    size: int
    modified: float
    status: str = "OK"

class FileTableModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.files = []  # List of FileItem
        self.headers = ["Name", "New Name", "Size", "Modified", "Status", "Path"]

    def rowCount(self, parent=QModelIndex()):
        return len(self.files)

    def columnCount(self, parent=QModelIndex()):
        return len(self.headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        
        item = self.files[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0: return item.original_name
            if col == 1: return item.new_name
            if col == 2: return f"{item.size / 1024:.2f} KB"
            if col == 3: return datetime.datetime.fromtimestamp(item.modified).strftime("%Y-%m-%d %H:%M")
            if col == 4: return item.status
            if col == 5: return str(item.path.parent)

        # Highlight changes
        if role == Qt.ItemDataRole.ForegroundRole and col == 1:
            if item.new_name != item.original_name:
                # Check duplicates (simple O(N) check for visual demo)
                all_new_names = [f.new_name for f in self.files]
                if all_new_names.count(item.new_name) > 1:
                    return QBrush(QColor("red"))
                return QBrush(QColor("green"))
            
        return None

    def headerData(self, section, orientation, role):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headers[section]
        return None

    def load_directory(self, path):
        self.beginResetModel()
        self.files = []
        p = pathlib.Path(path)
        if p.exists() and p.is_dir():
            try:
                for entry in p.iterdir():
                    if entry.is_file():
                        stat = entry.stat()
                        self.files.append(FileItem(
                            path=entry,
                            original_name=entry.name,
                            new_name=entry.name,
                            size=stat.st_size,
                            modified=stat.st_mtime
                        ))
            except PermissionError:
                pass 
        self.endResetModel()

    def update_previews(self, config: RenameConfig):
        """Recalculates new names based on config."""
        for i, item in enumerate(self.files):
            item.new_name = RenamingEngine.process(item.original_name, config, index=i+1)
        
        self.layoutChanged.emit()

# ==========================================
# 3. UI COMPONENTS (Logic Groups)
# ==========================================

class GroupWidget(QGroupBox):
    """Base class for the 14 functional groups."""
    config_changed = pyqtSignal() # Signal to notify main window to refresh

    def __init__(self, title, parent=None):
        super().__init__(title, parent)
        self.layout = QGridLayout()
        self.layout.setContentsMargins(4, 4, 4, 4)
        self.layout.setSpacing(4)
        self.setLayout(self.layout)
        
        # Style to match dense UI
        self.setStyleSheet("QGroupBox { font-weight: bold; } QLabel { font-weight: normal; }")

    def add_widget(self, widget, row, col, colspan=1):
        self.layout.addWidget(widget, row, col, 1, colspan)
        
        # Connect signals automatically
        if isinstance(widget, QLineEdit):
            widget.textChanged.connect(self.config_changed.emit)
        elif isinstance(widget, (QComboBox, QCheckBox, QSpinBox)):
            if hasattr(widget, 'currentTextChanged'):
                widget.currentTextChanged.connect(self.config_changed.emit)
            if hasattr(widget, 'stateChanged'):
                widget.stateChanged.connect(self.config_changed.emit)
            if hasattr(widget, 'valueChanged'):
                widget.valueChanged.connect(self.config_changed.emit)

# ==========================================
# 4. MAIN WINDOW
# ==========================================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PyBulkRename_Clone")
        self.resize(1280, 800)
        
        # State
        self.config = RenameConfig()
        
        # Main Layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(2, 2, 2, 2)

        # --- Top Section: Tree & Table ---
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # Left: Directory Tree
        self.fs_model = QFileSystemModel()
        self.fs_model.setRootPath(QDir.rootPath())
        self.fs_model.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot)
        
        self.tree = QTreeView()
        self.tree.setModel(self.fs_model)
        self.tree.setRootIndex(self.fs_model.index(QDir.rootPath()))
        self.tree.setColumnHidden(1, True)
        self.tree.setColumnHidden(2, True)
        self.tree.setColumnHidden(3, True)
        self.tree.setHeaderHidden(True)
        self.tree.clicked.connect(self.on_tree_clicked)
        
        # Right: File Grid
        self.table_model = FileTableModel()
        self.table = QTableView()
        self.table.setModel(self.table_model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setDefaultSectionSize(20) # Compact rows
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.setAlternatingRowColors(True)
        
        splitter.addWidget(self.tree)
        splitter.addWidget(self.table)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)
        
        main_layout.addWidget(splitter, stretch=2)

        # --- Bottom Section: Control Panel ---
        # Using ScrollArea because 14 groups take space
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        self.grid_layout = QGridLayout(scroll_content)
        self.grid_layout.setSpacing(5)
        
        # Build Groups
        self.build_groups()
        
        scroll.setWidget(scroll_content)
        # Fixed height for controls to mimic the "bottom half" feel
        scroll.setMinimumHeight(350) 
        main_layout.addWidget(scroll, stretch=1)

        # --- Footer: Actions ---
        footer = QFrame()
        footer_layout = QHBoxLayout(footer)
        
        self.btn_reset = QPushButton("Reset")
        self.btn_reset.clicked.connect(self.reset_ui)
        
        self.btn_rename = QPushButton("Rename")
        self.btn_rename.setStyleSheet("font-size: 14px; font-weight: bold; padding: 10px; min-width: 150px;")
        self.btn_rename.clicked.connect(self.execute_rename)
        
        footer_layout.addStretch()
        footer_layout.addWidget(self.btn_reset)
        footer_layout.addWidget(self.btn_rename)
        
        main_layout.addWidget(footer)

        # Debounce Timer for Preview
        self.preview_timer = QTimer()
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(100) # 100ms delay
        self.preview_timer.timeout.connect(self.refresh_preview)

    def on_tree_clicked(self, index):
        path = self.fs_model.filePath(index)
        self.table_model.load_directory(path)
        self.refresh_preview()

    def request_preview_update(self):
        """Called by UI widgets when changed. Debounces the actual calculation."""
        self.preview_timer.start()

    def refresh_preview(self):
        """Gather config from UI and update model."""
        self.update_config_from_ui()
        self.table_model.update_previews(self.config)

    def update_config_from_ui(self):
        """Scrapes the UI widgets to populate the Config object."""
        # 1. Regex
        self.config.regex_match = self.ui_regex_match.text()
        self.config.regex_replace = self.ui_regex_replace.text()
        self.config.regex_simple = self.ui_regex_simple.isChecked()

        # 2. Name
        self.config.name_mode = self.ui_name_mode.currentText()
        self.config.name_fixed = self.ui_name_fixed.text()

        # 3. Replace
        self.config.replace_str = self.ui_repl_match.text()
        self.config.replace_with = self.ui_repl_with.text()
        self.config.replace_case = self.ui_repl_case.isChecked()

        # 4. Case
        self.config.case_mode = self.ui_case_mode.currentText()

        # 5. Remove
        self.config.remove_first = self.ui_rem_first.value()
        self.config.remove_last = self.ui_rem_last.value()
        self.config.remove_from = self.ui_rem_from.value()
        self.config.remove_to = self.ui_rem_to.value()
        self.config.remove_chars = self.ui_rem_chars.text()

        # 7. Add
        self.config.add_prefix = self.ui_add_prefix.text()
        self.config.add_suffix = self.ui_add_suffix.text()
        self.config.add_insert = self.ui_add_insert.text()
        self.config.add_at_pos = self.ui_add_at.value()

        # 10. Numbering
        self.config.num_mode = self.ui_num_mode.currentText()
        self.config.num_start = self.ui_num_start.value()
        self.config.num_incr = self.ui_num_incr.value()
        self.config.num_pad = self.ui_num_pad.value()
        self.config.num_sep = self.ui_num_sep.text()
        self.config.num_at = self.ui_num_at.value()

        # 11. Extension
        self.config.ext_mode = self.ui_ext_mode.currentText()
        self.config.ext_fixed = self.ui_ext_fixed.text()

    def build_groups(self):
        """Constructs the dense UI grid of group boxes."""
        
        # --- Group 1: Regex ---
        g1 = GroupWidget("RegEx (1)")
        g1.config_changed.connect(self.request_preview_update)
        g1.add_widget(QLabel("Match:"), 0, 0)
        self.ui_regex_match = QLineEdit()
        g1.add_widget(self.ui_regex_match, 0, 1)
        g1.add_widget(QLabel("Replace:"), 1, 0)
        self.ui_regex_replace = QLineEdit()
        g1.add_widget(self.ui_regex_replace, 1, 1)
        self.ui_regex_simple = QCheckBox("Simple")
        g1.add_widget(self.ui_regex_simple, 2, 1)
        self.grid_layout.addWidget(g1, 0, 0)

        # --- Group 2: Name ---
        g2 = GroupWidget("Name (2)")
        g2.config_changed.connect(self.request_preview_update)
        self.ui_name_mode = QComboBox()
        self.ui_name_mode.addItems(["Keep", "Remove", "Fixed", "Reverse"])
        g2.add_widget(self.ui_name_mode, 0, 0, 2)
        self.ui_name_fixed = QLineEdit()
        self.ui_name_fixed.setPlaceholderText("Fixed Name")
        g2.add_widget(self.ui_name_fixed, 1, 0, 2)
        self.grid_layout.addWidget(g2, 0, 1)

        # --- Group 3: Replace ---
        g3 = GroupWidget("Replace (3)")
        g3.config_changed.connect(self.request_preview_update)
        g3.add_widget(QLabel("Replace:"), 0, 0)
        self.ui_repl_match = QLineEdit()
        g3.add_widget(self.ui_repl_match, 0, 1)
        g3.add_widget(QLabel("With:"), 1, 0)
        self.ui_repl_with = QLineEdit()
        g3.add_widget(self.ui_repl_with, 1, 1)
        self.ui_repl_case = QCheckBox("Match Case")
        g3.add_widget(self.ui_repl_case, 2, 0, 2)
        self.grid_layout.addWidget(g3, 0, 2)

        # --- Group 4: Case ---
        g4 = GroupWidget("Case (4)")
        g4.config_changed.connect(self.request_preview_update)
        self.ui_case_mode = QComboBox()
        self.ui_case_mode.addItems(["Same", "Lower", "Upper", "Title"])
        g4.add_widget(self.ui_case_mode, 0, 0)
        self.grid_layout.addWidget(g4, 0, 3)

        # --- Group 5: Remove ---
        g5 = GroupWidget("Remove (5)")
        g5.config_changed.connect(self.request_preview_update)
        
        g5.add_widget(QLabel("First n:"), 0, 0)
        self.ui_rem_first = QSpinBox()
        g5.add_widget(self.ui_rem_first, 0, 1)
        
        g5.add_widget(QLabel("Last n:"), 0, 2)
        self.ui_rem_last = QSpinBox()
        g5.add_widget(self.ui_rem_last, 0, 3)
        
        g5.add_widget(QLabel("From:"), 1, 0)
        self.ui_rem_from = QSpinBox()
        g5.add_widget(self.ui_rem_from, 1, 1)
        
        g5.add_widget(QLabel("To:"), 1, 2)
        self.ui_rem_to = QSpinBox()
        self.ui_rem_to.setMaximum(999)
        g5.add_widget(self.ui_rem_to, 1, 3)
        
        g5.add_widget(QLabel("Chars:"), 2, 0)
        self.ui_rem_chars = QLineEdit()
        g5.add_widget(self.ui_rem_chars, 2, 1, 3)
        self.grid_layout.addWidget(g5, 1, 0, 1, 2) # Span 2 cols

        # --- Group 7: Add ---
        g7 = GroupWidget("Add (7)")
        g7.config_changed.connect(self.request_preview_update)
        g7.add_widget(QLabel("Prefix:"), 0, 0)
        self.ui_add_prefix = QLineEdit()
        g7.add_widget(self.ui_add_prefix, 0, 1)
        g7.add_widget(QLabel("Suffix:"), 1, 0)
        self.ui_add_suffix = QLineEdit()
        g7.add_widget(self.ui_add_suffix, 1, 1)
        g7.add_widget(QLabel("Insert:"), 2, 0)
        self.ui_add_insert = QLineEdit()
        g7.add_widget(self.ui_add_insert, 2, 1)
        g7.add_widget(QLabel("At Pos:"), 3, 0)
        self.ui_add_at = QSpinBox()
        g7.add_widget(self.ui_add_at, 3, 1)
        self.grid_layout.addWidget(g7, 1, 2)

        # --- Group 10: Numbering ---
        g10 = GroupWidget("Numbering (10)")
        g10.config_changed.connect(self.request_preview_update)
        
        self.ui_num_mode = QComboBox()
        self.ui_num_mode.addItems(["None", "Prefix", "Suffix", "Insert"])
        g10.add_widget(QLabel("Mode:"), 0, 0)
        g10.add_widget(self.ui_num_mode, 0, 1)
        
        g10.add_widget(QLabel("Start:"), 1, 0)
        self.ui_num_start = QSpinBox()
        self.ui_num_start.setRange(0, 999999)
        self.ui_num_start.setValue(1)
        g10.add_widget(self.ui_num_start, 1, 1)
        
        g10.add_widget(QLabel("Incr:"), 2, 0)
        self.ui_num_incr = QSpinBox()
        self.ui_num_incr.setValue(1)
        g10.add_widget(self.ui_num_incr, 2, 1)
        
        g10.add_widget(QLabel("Pad:"), 3, 0)
        self.ui_num_pad = QSpinBox()
        self.ui_num_pad.setValue(0)
        g10.add_widget(self.ui_num_pad, 3, 1)
        
        g10.add_widget(QLabel("Sep:"), 4, 0)
        self.ui_num_sep = QLineEdit()
        g10.add_widget(self.ui_num_sep, 4, 1)

        g10.add_widget(QLabel("At:"), 5, 0)
        self.ui_num_at = QSpinBox()
        g10.add_widget(self.ui_num_at, 5, 1)
        
        self.grid_layout.addWidget(g10, 0, 4, 2, 1) # Vertical span

        # --- Group 11: Extension ---
        g11 = GroupWidget("Extension (11)")
        g11.config_changed.connect(self.request_preview_update)
        self.ui_ext_mode = QComboBox()
        self.ui_ext_mode.addItems(["Same", "Lower", "Upper", "Remove", "Fixed"])
        g11.add_widget(self.ui_ext_mode, 0, 0)
        self.ui_ext_fixed = QLineEdit()
        self.ui_ext_fixed.setPlaceholderText("Fixed Ext")
        g11.add_widget(self.ui_ext_fixed, 1, 0)
        self.grid_layout.addWidget(g11, 1, 3)

        # Placeholders for other groups (6, 8, 9, 12, 13, 14) to fill the visual grid
        # Group 6 Move/Copy
        g6 = GroupWidget("Move/Copy (6)")
        g6.add_widget(QLabel("Not Impl."), 0, 0)
        self.grid_layout.addWidget(g6, 2, 0)
        
        # Group 12 Filters
        g12 = GroupWidget("Filters (12)")
        g12.add_widget(QLabel("Mask: *"), 0, 0)
        self.grid_layout.addWidget(g12, 2, 1, 1, 2)
        
        # Group 13 Location
        g13 = GroupWidget("Location (13)")
        g13.add_widget(QLabel("Path: ./"), 0, 0)
        self.grid_layout.addWidget(g13, 2, 3, 1, 2)

    def reset_ui(self):
        # Simply clearing fields; a better way is to re-init the whole UI or individual widgets
        self.ui_regex_match.clear()
        self.ui_regex_replace.clear()
        self.ui_name_mode.setCurrentIndex(0)
        self.ui_name_fixed.clear()
        self.ui_repl_match.clear()
        self.ui_repl_with.clear()
        self.ui_case_mode.setCurrentIndex(0)
        self.ui_rem_first.setValue(0)
        self.ui_rem_last.setValue(0)
        self.ui_rem_from.setValue(0)
        self.ui_rem_to.setValue(0)
        self.ui_rem_chars.clear()
        self.ui_add_prefix.clear()
        self.ui_add_suffix.clear()
        self.ui_add_insert.clear()
        self.ui_num_mode.setCurrentIndex(0)
        self.ui_ext_mode.setCurrentIndex(0)
        self.ui_ext_fixed.clear()
        self.request_preview_update()

    def execute_rename(self):
        count = len(self.table_model.files)
        if count == 0:
            return

        reply = QMessageBox.question(
            self, "Confirm Rename", 
            f"Are you sure you want to rename {count} files?\nThis cannot be easily undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            success_count = 0
            errors = []
            
            for item in self.table_model.files:
                if item.original_name == item.new_name:
                    continue
                
                old_path = item.path
                new_path = old_path.parent / item.new_name
                
                try:
                    os.rename(old_path, new_path)
                    success_count += 1
                except Exception as e:
                    errors.append(f"{item.original_name}: {str(e)}")
            
            # Refresh view
            current_dir = self.fs_model.filePath(self.tree.currentIndex())
            if not current_dir:
                current_dir = QDir.rootPath()
            self.table_model.load_directory(current_dir)
            self.refresh_preview()
            
            msg = f"Renamed {success_count} files."
            if errors:
                msg += f"\n\nErrors ({len(errors)}):\n" + "\n".join(errors[:5])
            QMessageBox.information(self, "Result", msg)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Set a fusion style for better look across platforms
    app.setStyle("Fusion")
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
