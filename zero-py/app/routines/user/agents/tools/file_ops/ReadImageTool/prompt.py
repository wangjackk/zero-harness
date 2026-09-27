READ_IMAGE_TOOL_NAME = 'ReadImage'

DESCRIPTION = (
    'Reads an image file from the local filesystem and attaches it to the tool '
    'feedback message, so you can visually inspect it. '
    'The file_path parameter must be an absolute path, not a relative path. '
    'Supported formats: png, jpeg, gif, webp (recognized by content, extension is not required). '
    'Use it to view plots saved by plotting code, screenshots, downloaded images, or any local image file. '
    'Only useful when the current model supports image input.'
)
