# Python Substrate Interface Library
#
# Copyright 2018-2023 Stichting Polkascan (Polkascan Foundation).
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import binascii
from typing import Any, Optional, List, Union

from substrateinterface.exceptions import StorageFunctionNotFound

from scalecodec.base import ScaleBytes, ScaleTypeDef
from scalecodec.types import GenericMetadataVersioned, Tuple, Option, Array, U8
from scalecodec.utils.ss58 import ss58_decode
from scalecodec.base import RuntimeConfigurationObject, ScaleType
from .utils.hasher import blake2_256, two_x64_concat, xxh128, blake2_128, blake2_128_concat, identity, concat_hash_len


class StorageKey:
    """
    A StorageKey instance is a representation of a single state entry.

    Substrate uses a simple key-value data store implemented as a database-backed, modified Merkle tree.
    All of Substrate's higher-level storage abstractions are built on top of this simple key-value store.
    """

    def __init__(
            self, pallet: str, storage_function: str, params: list,
            data: bytes, value_scale_type: ScaleTypeDef, param_scale_types: List[ScaleTypeDef],
            metadata: GenericMetadataVersioned, runtime_config: RuntimeConfigurationObject
    ):
        self.pallet = pallet
        self.storage_function = storage_function
        self.params = params
        self.params_encoded = []
        self.data = data
        self.metadata = metadata
        self.runtime_config = runtime_config
        self.value_scale_type = value_scale_type
        self.param_scale_types = param_scale_types
        self.param_hashers = None
        self.metadata_storage_function = None

    @classmethod
    def create_from_data(cls, data: bytes, runtime_config: RuntimeConfigurationObject,
                         metadata: GenericMetadataVersioned, value_scale_type: str = None, pallet: str = None,
                         storage_function: str = None) -> 'StorageKey':
        """
        Create a StorageKey instance providing raw storage key bytes

        Parameters
        ----------
        data: bytes representation of the storage key
        runtime_config: RuntimeConfigurationObject
        metadata: GenericMetadataVersioned
        value_scale_type: type string of to decode result data
        pallet: name of pallet
        storage_function: name of storage function

        Returns
        -------
        StorageKey
        """
        if not value_scale_type and pallet and storage_function:
            metadata_pallet = metadata.get_metadata_pallet(pallet)

            if not metadata_pallet:
                raise StorageFunctionNotFound(f'Pallet "{pallet}" not found')

            storage_item = metadata_pallet.get_storage_function(storage_function)

            if not storage_item:
                raise StorageFunctionNotFound(f'Storage function "{pallet}.{storage_function}" not found')

            # Process specific type of storage function
            value_scale_type = storage_item.get_value_type_string()

        return cls(
            pallet=pallet, storage_function=storage_function, params=None,
            data=data, metadata=metadata,
            value_scale_type=value_scale_type, param_scale_types=None, runtime_config=runtime_config
        )

    @classmethod
    def create_from_storage_function(cls, pallet: str, storage_function: str, params: list,
                                     runtime_config: RuntimeConfigurationObject,
                                     metadata: GenericMetadataVersioned) -> 'StorageKey':
        """
        Create a StorageKey instance providing storage function details

        Parameters
        ----------
        pallet: name of pallet
        storage_function: name of storage function
        params: Optional list of parameters in case of a Mapped storage function
        runtime_config: RuntimeConfigurationObject
        metadata: GenericMetadataVersioned

        Returns
        -------
        StorageKey
        """
        storage_key_obj = cls(
            pallet=pallet, storage_function=storage_function, params=params,
            data=None, runtime_config=runtime_config, metadata=metadata, value_scale_type=None, param_scale_types=None
        )

        storage_key_obj.generate()

        return storage_key_obj

    def convert_storage_parameter(self, scale_type: str, value: Any):

        if type(value) is bytes:
            value = f'0x{value.hex()}'

        if scale_type == 'AccountId':
            if value[0:2] != '0x':
                return '0x{}'.format(ss58_decode(value, self.runtime_config.ss58_format))

        return value

    def to_hex(self) -> str:
        """
        Returns a Hex-string representation of current StorageKey data

        Returns
        -------
        str
            Hex string
        """
        if self.data:
            return f'0x{self.data.hex()}'

    def generate(self) -> bytes:
        """
        Generate a storage key for current specified pallet/function/params

        Returns
        -------
        bytes
        """

        # Search storage call in metadata
        metadata_pallet = self.metadata.get_metadata_pallet(self.pallet)

        if not metadata_pallet:
            raise StorageFunctionNotFound(f'Pallet "{self.pallet}" not found')

        self.metadata_storage_function = metadata_pallet.get_storage_function(self.storage_function)

        if not self.metadata_storage_function:
            raise StorageFunctionNotFound(f'Storage function "{self.pallet}.{self.storage_function}" not found')

        value_scale_type_id = self.metadata_storage_function.get_value_type_id()

        # TODO make generic
        if type(value_scale_type_id) is str:
            self.value_scale_type = self.metadata.portable_registry.get_type_def_primitive(value_scale_type_id)
        else:
            # Process specific type of storage function
            self.value_scale_type = self.metadata.portable_registry.get_scale_type_def(value_scale_type_id)

        param_type_id = self.metadata_storage_function.get_params_type_id()

        if param_type_id is None:
            self.param_scale_types = []
        else:
            param_types_def = self.metadata.portable_registry.get_scale_type_def(
                self.metadata_storage_function.get_params_type_id()
            )

            if type(param_types_def) is Tuple:
                self.param_scale_types = param_types_def.values
            else:
                self.param_scale_types = (param_types_def,)

        # if len(self.params) != len(param_types):
        #     raise ValueError(f'Storage function requires {len(param_types)} parameters, {len(self.params)} given')

        self.param_hashers = self.metadata_storage_function.get_param_hashers()

        storage_hash = xxh128(metadata_pallet.value['storage']['prefix'].encode()) + xxh128(self.storage_function.encode())

        # Encode parameters
        self.params_encoded = []
        if self.params:
            for idx, param in enumerate(self.params):
                if type(param) is ScaleBytes:
                    # Already encoded
                    self.params_encoded.append(param)
                else:
                    # param = self.convert_storage_parameter(param_types[idx], param)
                    param_obj = self.param_scale_types[idx].new()
                    self.params_encoded.append(param_obj.encode(param))

            for idx, param in enumerate(self.params_encoded):
                # Get hasher assiociated with param
                try:
                    param_hasher = self.param_hashers[idx]
                except IndexError:
                    raise ValueError(f'No hasher found for param #{idx + 1}')

                params_key = bytes()

                # Convert param to bytes
                if type(param) is str:
                    params_key += binascii.unhexlify(param)
                elif type(param) is ScaleBytes:
                    params_key += param.data
                elif isinstance(param, ScaleType):
                    params_key += param.data.data

                if not param_hasher:
                    param_hasher = 'Twox128'

                if param_hasher == 'Blake2_256':
                    storage_hash += blake2_256(params_key)

                elif param_hasher == 'Blake2_128':
                    storage_hash += blake2_128(params_key)

                elif param_hasher == 'Blake2_128Concat':
                    storage_hash += blake2_128_concat(params_key)

                elif param_hasher == 'Twox128':
                    storage_hash += xxh128(params_key)

                elif param_hasher == 'Twox64Concat':
                    storage_hash += two_x64_concat(params_key)

                elif param_hasher == 'Identity':
                    storage_hash += identity(params_key)

                else:
                    raise ValueError('Unknown storage hasher "{}"'.format(param_hasher))

        self.data = storage_hash

        return self.data

    def create_key_type_def(self, param_count: int) -> ScaleTypeDef:
        # Build storage key type
        key_items = []
        for n in range(param_count, len(self.param_scale_types)):
            key_items.append(Array(U8, concat_hash_len(self.param_hashers[n])))
            key_items.append(self.param_scale_types[n])
        return Tuple(*key_items)

    def decode_key_data(self, hex_data: str, param_count: int) -> ScaleType:
        item_key = self.create_key_type_def(param_count).new()
        item_key.decode(ScaleBytes('0x' + hex_data[len(self.to_hex()):]))
        return item_key

    def decode_scale_value(self, data: Optional[Union[ScaleBytes, str]] = None) -> ScaleType:
        """

        Parameters
        ----------
        data

        Returns
        -------

        """
        if type(data) is str:
            data = ScaleBytes(data)

        result_found = False

        if data is not None:
            change_scale_type = self.value_scale_type
            result_found = True
        elif self.metadata_storage_function.value['modifier'] == 'Default':
            # Fallback to default value of storage function if no result
            change_scale_type = self.value_scale_type
            data = ScaleBytes(self.metadata_storage_function.value_object['default'].value_object)
        else:
            # No result is interpreted as an Option<...> result
            change_scale_type = Option(self.value_scale_type)
            data = ScaleBytes(self.metadata_storage_function.value_object['default'].value_object)

        # Decode SCALE result data
        updated_obj = change_scale_type.new(metadata=self.metadata)
        updated_obj.decode(data)
        updated_obj.meta_info = {'result_found': result_found}

        return updated_obj

    def __repr__(self):
        if self.pallet and self.storage_function:
            return f'<StorageKey(pallet={self.pallet}, storage_function={self.storage_function}, params={self.params})>'
        elif self.data:
            return f'<StorageKey(data=0x{self.data.hex()})>'
        else:
            return repr(self)
