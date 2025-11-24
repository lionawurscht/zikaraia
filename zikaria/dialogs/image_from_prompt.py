import io
import textwrap

from aqt import mw
from aqt.qt import (
    QApplication,
    QDialog,
    QLabel,
    QPixmap,
    QPushButton,
    Qt,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from aqt.utils import showCritical, showInfo
from PIL import Image, ImageQt

from ..anki_utils import gemini_client_proxy
from ..config_utils import config, logger


class ImageFromPromptDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent or mw)
        self.setWindowTitle("Create Image from Prompt (Zikaria)")
        self.resize(600, 600)
        self.layout = QVBoxLayout(self)

        self.prompt_input = QTextEdit()
        self.prompt_input.setPlaceholderText("Enter your image prompt here...")
        self.layout.addWidget(QLabel("Image Prompt:"))
        self.layout.addWidget(self.prompt_input)

        self.submit_button = QPushButton("Generate Image")
        self.submit_button.clicked.connect(self.generate_image)
        self.layout.addWidget(self.submit_button)

        self.status_label = QLabel("")
        self.layout.addWidget(self.status_label)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layout.addWidget(self.image_label)

    def generate_image(self):
        prompt = self.prompt_input.toPlainText().strip()
        if not prompt:
            self.status_label.setText("Prompt is empty.")
            return

        self.status_label.setText("Generating image...")
        QApplication.processEvents()

        try:
            client = gemini_client_proxy.client
            if not client:
                showCritical("Google Gemini client is not configured.")
                return

            # Use Gemini's image generation (adjust model name as needed)
            response = client.models.generate_content(
                model=config.image_model_name,
                contents=[prompt],
                config=None,  # No special config needed for image
            )

            image = None
            # Official way to extract image from response
            for part in response.candidates[0].content.parts:
                if getattr(part, "inline_data", None) is not None:
                    image = Image.open(io.BytesIO(part.inline_data.data))
                    break

            if image is None:
                self.status_label.setText("No image returned from AI.")
                return

            qimage = ImageQt.ImageQt(image)
            pixmap = QPixmap.fromImage(qimage)
            self.image_label.setPixmap(
                pixmap.scaled(
                    512, 512, aspectRatioMode=Qt.AspectRatioMode.KeepAspectRatio
                )  # Qt.KeepAspectRatio
            )
            self.status_label.setText("Image generated successfully.")
        except Exception as e:
            logger.exception("Failed to generate image from prompt.")
            self.status_label.setText("There was an error generating the image.")
            wrapped_error = textwrap.fill(str(e), width=80)
            showInfo(f"Error:\n{wrapped_error}")


def open_image_from_prompt_dialog_action():
    dialog = ImageFromPromptDialog()
    dialog.exec()
