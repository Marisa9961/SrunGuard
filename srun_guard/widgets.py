"""Reusable duration editor with seconds/minutes/hours and long-duration presets."""

from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QHBoxLayout, QWidget

from .durations import format_duration


class CompactSpinBox(QDoubleSpinBox):
    def textFromValue(self, value):
        text = super().textFromValue(value)
        separator = self.locale().decimalPoint()
        return text.rstrip("0").rstrip(separator) if separator in text else text


class DurationEdit(QWidget):
    """Changing the unit preserves the exact integer-second duration (no truncation)."""

    def __init__(self, minimum: int, maximum: int, parent=None):
        super().__init__(parent)
        self.minimum = minimum
        self.maximum = maximum
        self._factor = 1
        self.number = CompactSpinBox()
        self.number.setDecimals(6)
        self.number.setRange(minimum, maximum)
        self.number.setSingleStep(1)
        self.unit = QComboBox()
        for text, factor in (("秒", 1), ("分钟", 60), ("小时", 3600)):
            self.unit.addItem(text, factor)
        self.presets = QComboBox()
        self.presets.addItem("快捷选择…", None)
        for seconds in (5, 30, 60, 300, 900, 1800, 3600, 10800, 21600,
                        43200, 86400, 259200, 604800):
            if minimum <= seconds <= maximum:
                self.presets.addItem(format_duration(seconds), seconds)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.number, 1)
        layout.addWidget(self.unit)
        layout.addWidget(self.presets)
        self.unit.currentIndexChanged.connect(self._unit_changed)
        self.presets.activated.connect(self._preset_selected)
        self.setToolTip(f"范围：{format_duration(minimum)} – {format_duration(maximum)}；切换单位保持时长不变")

    def value(self) -> int:
        return max(self.minimum, min(self.maximum, round(self.number.value() * self._factor)))

    def setValue(self, seconds: int):
        seconds = max(self.minimum, min(self.maximum, seconds))
        factor = 3600 if seconds % 3600 == 0 else 60 if seconds % 60 == 0 else 1
        self.unit.blockSignals(True)
        self.unit.setCurrentIndex(self.unit.findData(factor))
        self.unit.blockSignals(False)
        self._display(seconds, factor)

    def _display(self, seconds: int, factor: int):
        self._factor = factor
        self.number.setRange(self.minimum / factor, self.maximum / factor)
        self.number.setValue(seconds / factor)

    def _unit_changed(self, index: int):
        self._display(self.value(), self.unit.itemData(index))

    def _preset_selected(self, index: int):
        seconds = self.presets.itemData(index)
        if seconds is not None:
            self.setValue(seconds)
        self.presets.setCurrentIndex(0)
