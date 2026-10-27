"""Dialog used by the ask_user agent tool."""

from __future__ import annotations

from pathlib import Path

from PyQt6 import uic
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QRadioButton


MAX_CHOICES = 8


class AskUserDialog(QDialog):
    def __init__(self, question: str, choices: list[str], parent=None) -> None:
        super().__init__(parent)
        uic.loadUi(str(Path(__file__).with_name("ask_user_dialog.ui")), self)
        self.answer: str | None = None
        self.answer_source: str = ""
        self.question = question
        self._radios: list[QRadioButton] = []

        self.questionLabel.setText(question)
        clean_choices = [str(choice).strip() for choice in choices if str(choice).strip()]
        clean_choices = clean_choices[:MAX_CHOICES]
        for choice in clean_choices:
            radio = QRadioButton(choice, self.choicesHost)
            radio.toggled.connect(self._clear_error)
            self.choicesLayout.addWidget(radio)
            self._radios.append(radio)
        if not clean_choices:
            self.choicesTitle.hide()
            self.choicesScroll.hide()
            self.answerLabel.setText("Type your answer")
        self.choicesLayout.addStretch(1)

        self.answerEdit.textChanged.connect(self._clear_error)
        self.answerEdit.returnPressed.connect(self._accept_answer)
        self.buttonBox.accepted.connect(self._accept_answer)
        self.buttonBox.rejected.connect(self.reject)
        ok = self.buttonBox.button(QDialogButtonBox.StandardButton.Ok)
        if ok:
            ok.setText("Answer")
            ok.setDefault(True)
        self.errorLabel.hide()
        self.answerEdit.setFocus()

    def _clear_error(self) -> None:
        if self.errorLabel.isVisible():
            self.errorLabel.hide()

    def _accept_answer(self) -> None:
        typed = self.answerEdit.text().strip()
        selected = ""
        for radio in self._radios:
            if radio.isChecked():
                selected = radio.text().strip()
                break
        if typed:
            self.answer = typed
            self.answer_source = "free_form"
        elif selected:
            self.answer = selected
            self.answer_source = "choice"
        else:
            self.errorLabel.setText("Choose an option or type your own answer.")
            self.errorLabel.show()
            return
        self.accept()
