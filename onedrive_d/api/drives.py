"""
Abstraction of root resources and drive resources. In the API, a "*dir" call accesses a directory, a "*file" call
accesses a file, and a "*item" call accesses either a directory or a file.
https://github.com/OneDrive/onedrive-api-docs#root-resources
"""

import json

import requests

from onedrive_d.api import facets
from onedrive_d.api import items
from onedrive_d.api import options
from onedrive_d.api import resources
from onedrive_d.common import logger_factory
from onedrive_d.common import drive_config


class DriveRoot:
    """
    An entry point to get associated drives.
    """

    def __init__(self, account):
        """
        :param onedrive_d.api.accounts.PersonalAccount | onedrive_d.api.accounts.BusinessAccount account:
        :param dict[str, DriveObject] cached_drives:
        """
        self.account = account
        self._cached_drives = {}

    def add_cached_drive(self, account_id, account_type, drive):
        if account_id != self.account.profile.user_id:
            raise ValueError('The specified drive should not belong to the drive root.')
        elif account_type != self.account.TYPE:
            raise ValueError('The specified drive does not belong to an account of type %s' % self.account.TYPE)
        self._cached_drives[drive.drive_id] = drive

    def get_all_drives(self):
        """
        :rtype dict[str, DriveObject]: a dictionary of all drives with keys being drive IDs.
        """
        uri = self.account.client.API_URI + '/drives'
        request = self.account.session.get(uri)
        all_drives = {d['id']: DriveObject(self, d, drive_config.DriveConfig.default_config())
                      for d in request.json()['value']}
        return all_drives

    def get_default_drive(self):
        """
        An alias for get_drive(drive_id=None)
        :return onedrive_d.api.drives.DriveObject:
        """
        return self.get_drive(None)

    def purge_drive_cache(self, drive_id):
        del self._cached_drives[drive_id]

    def get_drive(self, drive_id=None, skip_cache=False):
        """
        :param str | None drive_id: (Optional) ID of the target Drive. Use None to get default Drive.
        :return onedrive_d.api.drives.DriveObject:
        """
        if drive_id in self._cached_drives:
            drive = self._cached_drives[drive_id]
            if skip_cache:
                drive.refresh()
            return drive
        uri = self.account.client.API_URI + '/drive'
        if drive_id is not None:
            uri = uri + 's/' + drive_id
        request = self.account.session.get(uri)
        d = DriveObject(self, request.json(), drive_config.DriveConfig.default_config())
        self._cached_drives[d.drive_id] = d
        return d


class DriveObject:
    """
    Abstracts a specific Drive resource. All items.OneDriveItem objects are generated by DriveObject API.
    """

    VERSION_KEY = '@version'
    VERSION_VALUE = 0

    logger = logger_factory.get_logger('DriveObject')

    def __init__(self, root, data, config):
        """
        :param onedrive_d.api.drives.OneDriveRoot root: The parent root object.
        :param dict[str, str | int | dict] data: The deserialized Drive dictionary.
        :param onedrive_d.api.common.drive_config.DriveConfig config: Drive configuration.
        """
        self._data = data
        self.config = config
        self.root = root
        self.is_default = root.account.profile.user_id.lower() == self.drive_id.lower()
        if self.is_default:
            self.drive_path = '/drive'
        else:
            self.drive_path = '/drives/' + data['id']
        self.drive_uri = root.account.client.API_URI

    @property
    def drive_id(self):
        """
        Return the drive ID.
        :rtype: str
        """
        return self._data['id']

    @property
    def type(self):
        """
        Return a string representing the drive's type. {'personal', 'business'}
        :rtype: str
        """
        return self._data['driveType']

    @property
    def quota(self):
        """
        :rtype: onedrive_d.api.facets.QuotaFacet
        """
        return facets.QuotaFacet(self._data['quota'])

    def refresh(self):
        """
        Refresh metadata of the drive object.
        """
        self.root.purge_drive_cache(self.drive_id)
        new_drive = self.root.get_drive(self.drive_id)
        # noinspection PyProtectedMember
        self._data = new_drive._data
        del new_drive

    def get_item_uri(self, item_id=None, item_path=None):
        """
        Generate URL to the specified item. If both item_id and item_path are None, return root item.
        :param str | None item_id: (Optional) ID of the specified item.
        :param str | None item_path: (Optional) Path to the specified item.
        :rtype: str
        """
        uri = self.drive_uri
        if item_id is not None:
            uri += self.drive_path + '/items/' + item_id
        elif item_path is not None and item_path != self.drive_path + '/root:':
            uri += item_path
        else:
            uri += self.drive_path + '/root'
        return uri

    def get_root_dir(self, list_children=True):
        return self.get_item(None, None, list_children)

    def build_item(self, data):
        return items.OneDriveItem(self, data)

    def get_item(self, item_id=None, item_path=None, list_children=True):
        """
        Retrieve the metadata of an item from OneDrive server.
        :param str | None item_id:  ID of the item. Required if item_path is None.
        :param str | None item_path: Path to the item relative to drive root. Required if item_id is None.
        :rtype: onedrive_d.api.items.OneDriveItem
        """
        uri = self.get_item_uri(item_id, item_path)
        if list_children:
            uri += '?expand=children'
        request = self.root.account.session.get(uri)
        return items.OneDriveItem(self, request.json())

    def get_children(self, item_id=None, item_path=None):
        """
        Assuming the target item is a directory, return a collection of all its children items.
        :param str | None item_id: (Optional) ID of the target directory.
        :param str | None item_path: (Optional) Path to the target directory.
        :rtype: onedrive_d.api.items.ItemCollection
        """
        uri = self.get_item_uri(item_id, item_path)
        if item_path is not None:
            uri += ':'
        uri += '/children'
        request = self.root.account.session.get(uri)
        return items.ItemCollection(self, request.json())

    def create_dir(self, name, parent_id=None, parent_path=None,
                   conflict_behavior=options.NameConflictBehavior.DEFAULT):
        """
        Create a new directory under the specified parent directory.
        :param str name: Name of the new directory.
        :param str | None parent_id: (Optional) ID of the parent directory item.
        :param str | None parent_path: (Optional) Path to the parent directory item.
        :param str conflict_behavior: (Optional) What to do if name exists. One value from options.nameConflictBehavior.
        :rtype: onedrive_d.api.items.OneDriveItem
        """
        data = {
            'name': name,
            'folder': {},
            '@name.conflictBehavior': conflict_behavior
        }
        uri = self.get_item_uri(parent_id, parent_path) + '/children'
        request = self.root.account.session.post(uri, json=data, ok_status_code=requests.codes.created)
        return items.OneDriveItem(self, request.json())

    def upload_file(self, filename, data, size, parent_id=None, parent_path=None,
                    conflict_behavior=options.NameConflictBehavior.REPLACE):
        """
        Upload a file object to the specified parent directory, the method of which is determined by file size.
        :param str filename: Name of the remote file.
        :param file data: An opened file object available for reading.
        :param int size: Size of the content to upload.
        :param str | None parent_id: (Optional) ID of the parent directory.
        :param str | None parent_path: (Optional) Path to the parent directory.
        :param str conflict_behavior: (Optional) Specify the behavior to use if the file already exists.
        :rtype: onedrive_d.api.items.OneDriveItem
        """
        if size <= self.config.max_put_size_bytes:
            return self.put_file(filename, data, parent_id, parent_path, conflict_behavior)
        else:
            return self.put_large_file(filename, data, size, parent_id, parent_path, conflict_behavior)

    def put_large_file(self, filename, data, size, parent_id=None, parent_path=None,
                       conflict_behavior=options.NameConflictBehavior.REPLACE):
        """
        Upload a large file by splitting it into fragments.
        https://github.com/OneDrive/onedrive-api-docs/blob/master/items/upload_large_files.md
        :param str filename: Name of the remote file.
        :param file data: An opened file object available for reading.
        :param int size: Size of the content to upload.
        :param str | None parent_id: (Optional) ID of the parent directory.
        :param str | None parent_path: (Optional) Path to the parent directory.
        :param str conflict_behavior: (Optional) Specify the behavior to use if the file already exists.
        :rtype: onedrive_d.api.items.OneDriveItem
        """
        # Create an upload session.
        if parent_id is not None:
            parent_id += ':'
        uri = self.get_item_uri(parent_id, parent_path) + '/' + filename + ':/upload.createSession'
        payload = {'item': {'name': filename}}
        if conflict_behavior != options.NameConflictBehavior.REPLACE:
            payload['item']['@name.conflictBehavior'] = conflict_behavior
        size_str = str(size)
        request = self.root.account.session.post(uri, json=payload)
        current_session = resources.UploadSession(request.json())

        # Upload content.
        expected_ranges = [(0, size - 1)]  # Use local value rather than that given in session.
        while len(expected_ranges) > 0:  # Ranges must come in order
            f, t = expected_ranges.pop(0)  # Both inclusive
            if t is None or t >= size:
                t = size - 1
            next_cursor = f + self.config.max_put_size_bytes
            if t >= next_cursor:
                expected_ranges.insert(0, (next_cursor, t))
                t = next_cursor - 1
            data.seek(f)
            chunk = data.read(t - f + 1)
            headers = {
                'Content-Range': str(f) + '-' + str(t) + '/' + size_str
            }
            request = self.root.account.session.put(current_session.upload_url, data=chunk, headers=headers,
                                                    ok_status_code=requests.codes.accepted)
            current_session.update(request.json())
            # TODO: handle timeout error
            # https://github.com/OneDrive/onedrive-api-docs/blob/master/items/upload_large_files.md#request-upload-status

    def put_file(self, filename, data, parent_id=None, parent_path=None,
                 conflict_behavior=options.NameConflictBehavior.REPLACE):
        """
        Use HTTP PUT to upload a file that is relatively small (less than 100M).
        :param str filename: Name of the remote file.
        :param file data: An opened file object available for reading.
        :param str | None parent_id: (Optional) ID of the parent directory.
        :param str | None parent_path: (Optional) Path to the parent directory.
        :param str conflict_behavior: (Optional) Specify the behavior to use if the file already exists.
        :rtype: onedrive_d.api.items.OneDriveItem
        """
        if parent_id is not None:
            parent_id += ':'
        uri = self.get_item_uri(parent_id, parent_path) + '/' + filename + ':/content'
        if conflict_behavior != options.NameConflictBehavior.REPLACE:
            uri += '?@name.conflictBehavior=' + conflict_behavior
        request = self.root.account.session.put(uri, data=data, ok_status_code=requests.codes.created)
        return items.OneDriveItem(self, request.json())

    def download_file(self, file, size, item_id=None, item_path=None):
        """
        Download the target item to target file object. If the file is too large, download by fragments.
        :param file file: An open file object available for writing binary data.
        :param int size: Expected size of the item.
        :param str | None item_id: ID of the target file.
        :param str | None item_path: Path to the target file.
        """
        if size <= self.config.max_get_size_bytes:
            self.get_file_content(item_id, item_path, file=file)
            return
        t = 0
        while t < size:
            f = t
            t += self.config.max_get_size_bytes - 1  # Both inclusive.
            if t >= size:
                t = size - 1
            self.get_file_content(item_id, item_path, range_bytes=(f, t), file=file)
            t += 1

    def get_file_content(self, item_id=None, item_path=None, range_bytes=None, file=None):
        """
        Get the content of an item.
        :param str | None item_id: ID of the target file.
        :param str | None item_path: Path to the target file.
        :param (int, int) | None range_bytes: Range of the bytes to download.
        :param file | None file: An opened file object. If set, write the content there. Otherwise return the content.
        :rtype: bytes
        """
        uri = self.get_item_uri(item_id, item_path) + '/content'
        if range_bytes is None:
            headers = None
            ok_status_code = requests.codes.ok
        else:
            headers = {'Range': 'bytes=%d-%d' % range_bytes}
            ok_status_code = requests.codes.partial
        request = self.root.account.session.get(uri, headers=headers, ok_status_code=ok_status_code)
        if file is not None:
            file.write(request.content)
        else:
            return request.content

    def delete_item(self, item_id=None, item_path=None):
        """
        https://github.com/OneDrive/onedrive-api-docs/blob/master/items/delete.md
        Delete the specified item on OneDrive server.
        :param str | None item_id:  ID of the item. Required if item_path is None.
        :param str | None item_path: Path to the item relative to drive root. Required if item_id is None.
        """
        uri = self.get_item_uri(item_id, item_path)
        self.root.account.session.delete(uri, ok_status_code=requests.codes.no_content)

    def update_item(self, item_id=None, item_path=None,
                    new_name=None,
                    new_description=None,
                    new_parent_reference=None,
                    new_file_system_info=None):
        """
        Update the metadata of the specified item.
        :param str | None item_id: (Optional) ID of the target item.
        :param str | None item_path: (Optional) Path to the target item.
        :param str | None new_name: (Optional) If set, update the item metadata with the new name.
        :param str | None new_description: (Optional) If set, update the item metadata with the new description.
        :param onedrive_d.api.resources.ItemReference | None new_parent_reference: (Optional) If set,
        move the item.
        :param onedrive_d.api.facets.FileSystemInfoFacet | None new_file_system_info: (Optional) If set, update the
        client-wise timestamps.
        :rtype: onedrive_d.api.items.OneDriveItem
        """
        if item_id is None and item_path is None:
            raise ValueError('Root is immutable. A specific item is required.')
        data = {}
        if new_name is not None:
            data['name'] = new_name
        if new_description is not None:
            data['description'] = new_description
        if new_parent_reference is not None:
            data['parentReference'] = new_parent_reference.data
        if new_file_system_info is not None:
            data['fileSystemInfo'] = new_file_system_info.data
        if len(data) == 0:
            raise ValueError('Nothing is to change.')
        uri = self.get_item_uri(item_id, item_path)
        request = self.root.account.session.patch(uri, data)
        return items.OneDriveItem(self, request.json())

    def copy_item(self, dest_reference, item_id=None, item_path=None, new_name=None):
        """
        Copy an item (including any children) on OneDrive under a new parent.
        :param onedrive_d.api.resources.ItemReference dest_reference: Reference to new parent.
        :param str | None item_id: (Optional) ID of the source item. Required if item_path is None.
        :param str | None item_path: (Optional) Path to the source item. Required if item_id is None.
        :param str | None new_name: (Optional) If set, use this name for the copied item.
        :rtype: onedrive_d.api.resources.AsyncCopySession
        """
        if not isinstance(dest_reference, resources.ItemReference):
            raise ValueError('Destination should be an ItemReference object.')
        if item_id is None and item_path is None:
            raise ValueError('Source of copy must be specified.')
        uri = self.get_item_uri(item_id, item_path)
        if item_path is not None:
            uri += ':'
        uri += '/action.copy'
        data = {'parentReference': dest_reference.data}
        if new_name is not None:
            data['name'] = new_name
        headers = {'Prefer': 'respond-async'}
        request = self.root.account.session.post(uri, json=data, headers=headers)
        return resources.AsyncCopySession(self, request.headers)

    def get_thumbnail(self):
        raise NotImplementedError('The API feature is not used yet.')

    def search(self, keyword, select=None, item_id=None, item_path=None):
        """
        Use a keyword to search for items within the specified directory (default: root).
        :param str keyword: Keyword for the search.
        :param [str] | None select: Only fetch the specified fields.
        :param str | None item_id: (Optional) ID of the item to search within.
        :param str | None item_path: (Optional) Path to the item to search within.
        :return onedrive_d.api.items.ItemCollection:
        """
        params = {'q': keyword}
        if select is not None:
            params['select'] = ','.join(select)
        uri = self.get_item_uri(item_id, item_path) + '/view.search'
        request = self.root.account.session.get(uri, params=params)
        return items.ItemCollection(self, request.json())

    def get_changes(self):
        raise NotImplementedError('The API feature is not used yet.')

    def get_special_dir(self, name):
        raise NotImplementedError('The API feature is not used yet.')

    def dump(self):
        data = {
            'config_dump': self.config.dump(),
            'data': self._data,
            self.VERSION_KEY: self.VERSION_VALUE
        }
        return json.dumps(data)

    @classmethod
    def load(cls, drive_root, account_id, account_type, s):
        data = json.loads(s)
        drive = DriveObject(drive_root, data['data'], drive_config.DriveConfig.load(data['config_dump']))
        try:
            drive_root.add_cached_drive(account_id, account_type, drive)
        except ValueError as e:
            cls.logger.warning('Faild to register deserialized drive %s to drive root: %s', drive.drive_id, e)
        return drive
