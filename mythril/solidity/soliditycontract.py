"""This module contains representation classes for Solidity files, contracts
and source mappings."""
from typing import Dict, Set

import mythril.laser.ethereum.util as helper
from mythril.ethereum.evmcontract import EVMContract
from mythril.ethereum.util import get_solc_json
from mythril.exceptions import NoContractFoundError


class SourceMapping:
    def __init__(self, solidity_file_idx, offset, length, lineno, mapping):
        """Representation of a source mapping for a Solidity file."""

        self.solidity_file_idx = solidity_file_idx
        self.offset = offset
        self.length = length
        self.lineno = lineno
        self.solc_mapping = mapping


class SolidityFile:
    """Representation of a file containing Solidity code."""

    def __init__(self, filename: str, data: str, full_contract_source: Set[str]):
        """
        Metadata class containing data regarding a specific solidity file
        :param filename: The filename of the solidity file
        :param data: The code of the solidity file
        :param full_contract_source: The set of contract source mappings of all the contracts in the file
        """
        self.filename = filename
        self.data = data
        self.full_contract_source = full_contract_source


class SourceCodeInfo:
    def __init__(self, filename, lineno, code, mapping):
        """Metadata class containing a code reference for a specific file."""

        self.filename = filename
        self.lineno = lineno
        self.code = code
        self.solc_mapping = mapping


def get_contracts_from_file(input_file, solc_args=None, solc_binary="solc"):
    """

    :param input_file:
    :param solc_args:
    :param solc_binary:
    """
    data = get_solc_json(input_file, solc_args=solc_args, solc_binary=solc_binary)

    try:
        for key, contract in data["contracts"].items():
            filename, name = key.split(":")
            if filename == input_file and len(contract["bin-runtime"]):
                yield SolidityContract(
                    input_file=input_file,
                    name=name,
                    solc_args=solc_args,
                    solc_binary=solc_binary,
                )
    except KeyError:
        raise NoContractFoundError


class SolidityContract(EVMContract):
    """Representation of a Solidity contract."""

    def __init__(self, input_file, name=None, solc_args=None, solc_binary="solc"):
        data = get_solc_json(input_file, solc_args=solc_args, solc_binary=solc_binary)

        self.solidity_files = []

        for filename in data["sourceList"]:
            with open(filename, "r", encoding="utf-8") as file:
                code = file.read()
                full_contract_sources = self.get_full_contract_sources(
                    data["sources"][filename]["AST"]
                )
                self.solidity_files.append(
                    SolidityFile(filename, code, full_contract_sources)
                )

        has_contract = False

        # If a contract name has been specified, find the bytecode of that specific contract
        srcmap_constructor = []
        srcmap = []
        if name:
            for key, contract in sorted(data["contracts"].items()):
                filename, _name = key.split(":")

                if (
                    filename == input_file
                    and name == _name
                    and len(contract["bin-runtime"])
                ):
                    code = contract["bin-runtime"]
                    creation_code = contract["bin"]
                    srcmap = contract["srcmap-runtime"].split(";")
                    srcmap_constructor = contract["srcmap"].split(";")
                    has_contract = True
                    break

        # If no contract name is specified, get the last bytecode entry for the input file

        else:
            for key, contract in sorted(data["contracts"].items()):
                filename, name = key.split(":")

                if filename == input_file and len(contract["bin-runtime"]):
                    code = contract["bin-runtime"]
                    creation_code = contract["bin"]
                    srcmap = contract["srcmap-runtime"].split(";")
                    srcmap_constructor = contract["srcmap"].split(";")
                    has_contract = True

        if not has_contract:
            raise NoContractFoundError

        self.mappings = []

        self.constructor_mappings = []

        self._get_solc_mappings(srcmap)
        self._get_solc_mappings(srcmap_constructor, constructor=True)

        super().__init__(code, creation_code, name=name)

    @staticmethod
    def get_full_contract_sources(ast: Dict) -> Set[str]:
        """
        Takes AST and returns the source map of the contract
        :param ast: AST of the contract
        :return: The source map
        """
        source_map = set()
        for child in ast["children"]:
            if "contractKind" in child["attributes"]:
                source_map.add(child["src"])
        return source_map

    def get_source_info(self, address, constructor=False):
        """

        :param address:
        :param constructor:
        :return:
        """
        disassembly = self.creation_disassembly if constructor else self.disassembly
        mappings = self.constructor_mappings if constructor else self.mappings
        index = helper.get_instruction_index(disassembly.instruction_list, address)

        solidity_file = self.solidity_files[mappings[index].solidity_file_idx]
        filename = solidity_file.filename

        offset = mappings[index].offset
        length = mappings[index].length

        code = solidity_file.data.encode("utf-8")[offset : offset + length].decode(
            "utf-8", errors="ignore"
        )
        lineno = mappings[index].lineno
        return SourceCodeInfo(filename, lineno, code, mappings[index].solc_mapping)

    def _is_autogenerated_code(self, offset: int, length: int, file_index: int) -> bool:
        """
        Checks whether the code is autogenerated or not
        :param offset: offset of the code
        :param length: length of the code
        :param file_index: file the code corresponds to
        :return: True if the code is internally generated, else false
        """
        # Handle internal compiler files
        if file_index == -1:
            return True
        # Handle the common code src map for the entire code.
        if (
            "{}:{}:{}".format(offset, length, file_index)
            in self.solidity_files[file_index].full_contract_source
        ):
            return True

        return False

    def _get_solc_mappings(self, srcmap, constructor=False):
        """

        :param srcmap:
        :param constructor:
        """
        mappings = self.constructor_mappings if constructor else self.mappings
        prev_item = ""
        for item in srcmap:
            if item == "":
                item = prev_item
            mapping = item.split(":")

            if len(mapping) > 0 and len(mapping[0]) > 0:
                offset = int(mapping[0])

            if len(mapping) > 1 and len(mapping[1]) > 0:
                length = int(mapping[1])

            if len(mapping) > 2 and len(mapping[2]) > 0:
                idx = int(mapping[2])

            if self._is_autogenerated_code(offset, length, idx):
                lineno = None
            else:
                lineno = (
                    self.solidity_files[idx]
                    .data.encode("utf-8")[0:offset]
                    .count("\n".encode("utf-8"))
                    + 1
                )
            prev_item = item
            mappings.append(SourceMapping(idx, offset, length, lineno, item))
