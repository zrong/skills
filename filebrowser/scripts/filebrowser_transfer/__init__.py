"""FileBrowser transfer skill runtime.

Import submodules directly (e.g. ``filebrowser_transfer.filebrowser``,
``filebrowser_transfer.config``); pulling symbols up here would force
``boto3`` to load on every ``import filebrowser_transfer``, which is
unnecessary for skills that only need the file-management client.
"""
