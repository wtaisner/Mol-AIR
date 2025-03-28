import builtins
import csv
import warnings
from collections import defaultdict
from dataclasses import dataclass
from typing import (Callable, Generic, Iterable, Optional, Type,
                    TypeVar, Union, Any)

import yaml

warnings.filterwarnings(action="ignore")
warnings.filterwarnings(action="default")
import inspect
import json
import os
import random
import sys
from contextlib import contextmanager

import numpy as np
import selfies as sf
import torch
import torch.backends.cudnn as cudnn
from rdkit import Chem
from rdkit.Chem import Draw
from tqdm import tqdm
import wandb

T = TypeVar("T")

_random_seed = None


def seed(value: int):
    global _random_seed
    _random_seed = value
    torch.manual_seed(value)
    torch.cuda.manual_seed(value)
    torch.cuda.manual_seed_all(value)
    np.random.seed(value)
    cudnn.benchmark = False
    cudnn.deterministic = True
    random.seed(value)


class LoggerException(Exception):
    pass


class logger:

    _enabled = False
    _LOG_BASE_DIR: str = "outputs"
    _log_dir: Optional[str] = None
    _run_id: Any = None  # Store the WandB run ID
    _has_logged_data: bool = False  # Track if any data has been logged

    @classmethod
    def enabled(cls) -> bool:
        return cls._enabled

    @classmethod
    def enable(cls, id: str, project_name: str = "Mol-AIR",
               entity: str = "witold_taisner"):
        if not cls._enabled:
            cls._enabled = True
            cls._log_dir = f"{cls._LOG_BASE_DIR}/{id}"
            os.makedirs(cls._log_dir, exist_ok=True)

            # Initialize WandB (but don't start the run yet)
            try:
                # Use anonymous mode to allow initialization without API key
                wandb.init(
                    project=project_name,
                    entity=entity,
                    name=id,
                    dir=cls._log_dir,
                    mode="online" if os.getenv("WANDB_API_KEY") else "offline",  # Use offline mode if no API key
                    anonymous="allow" #allow anonymous init
                )

                cls._run_id = wandb.run.name # type: ignore
            except Exception as e:
                raise LoggerException(f"Failed to initialize WandB: {e}")

        else:
            raise LoggerException("logger is already enabled")
        

    @classmethod
    def disable(cls):
        if cls._enabled:
            if cls._has_logged_data:  # Only finish if data was logged
                wandb.finish()      

            cls._log_dir = None
            cls._run_id = None
            cls._enabled = False
            cls._has_logged_data = False  # Reset for the next run
        else:
            raise LoggerException("logger is already disabled")

    @classmethod
    def print(cls, message: str, prefix: str = "[Mol-AIR] "):
        builtins.print(f"{prefix}{message}")


    @classmethod
    def log_data(cls, key, value, t):
        if not cls._enabled: #check if logger enabled
            raise LoggerException("Logger is not enabled.  Call logger.enable() first.")
        
        if not cls._has_logged_data: #first data logged
             cls._has_logged_data = True

        wandb.log({key: value, "step": t})


    @classmethod
    def dir(cls) -> str:
        if cls._log_dir is None:
            raise LoggerException("logger is not enabled")
        return cls._log_dir

    @classmethod
    def plot_logs(cls):
        print("Logs are automatically plotted on the WandB website.")

    @classmethod
    def log_config(cls, config: dict):
        """Logs the configuration to WandB."""
        if not cls._enabled:
            raise LoggerException("Logger is not enabled.")
        
        if not cls._has_logged_data:
            cls._has_logged_data = True

        wandb.config.update(config)


class TextInfoBox:
    def __init__(self, right_margin: int = 10) -> None:
        self._texts = []
        self._right_margin = right_margin
        self._max_text_len = 0

    def add_text(self, text: Optional[str]) -> "TextInfoBox":
        if text is None:
            return self
        self._max_text_len = max(self._max_text_len, len(text))
        self._texts.append((f" {text} ", " "))
        return self

    def add_line(self, marker: str = "-") -> "TextInfoBox":
        if len(marker) != 1:
            raise ValueError(f"marker must be one character, but {marker}")
        self._texts.append(("", marker))
        return self

    def make(self) -> str:
        text_info_box = f"+{self._horizontal_line()}+\n"
        for text, marker in self._texts:
            text_info_box += f"|{text}{marker * (self._max_space_len - len(text))}|\n"
        text_info_box += f"+{self._horizontal_line()}+"
        return text_info_box

    def _horizontal_line(self, marker: str = "-") -> str:
        return marker * (self._max_space_len)

    @property
    def _max_space_len(self) -> int:
        return self._max_text_len + self._right_margin


def load_yaml(file_path: str) -> dict:
    with open(file_path, "r") as f:
        return yaml.load(f, Loader=yaml.FullLoader)


def save_yaml(file_path: str, data: dict):
    with open(file_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False)


def dict_from_keys(d: dict, keys: Iterable) -> dict:
    matched_dict = dict()
    dict_keys = d.keys()
    for key in keys:
        if key in dict_keys:
            matched_dict[key] = d[key]
    return matched_dict


def instance_from_dict(class_type: Type[T], d: dict) -> T:
    params = tuple(inspect.signature(class_type).parameters)
    param_dict = dict_from_keys(d, params)
    return class_type(**param_dict)


def exists_dir(directory) -> bool:
    return os.path.exists(directory)


def file_exists(file_path: str) -> bool:
    return os.path.isfile(file_path)


def try_create_dir(directory):
    """If there's no directory, create it."""
    try:
        if not exists_dir(directory):
            os.makedirs(directory)
    except OSError:
        print("Error: Failed to create the directory.")


class ItemUpdateFollower(Generic[T]):
    def __init__(self, init_item: T, include_init: bool = True):
        self._item = init_item
        self._items = []
        if include_init:
            self._items.append(init_item)

    def update(self, item: T):
        self._item = item
        self._items.append(item)

    def popall(self) -> tuple[T, ...]:
        items = tuple(self._items)
        self._items.clear()
        return items

    @property
    def item(self) -> T:
        return self._item

    def __len__(self) -> int:
        return len(self._items)


def moving_average(values: np.ndarray, n: Optional[int] = None, smooth: Optional[float] = None):
    if (n is None and smooth is None) or (n is not None and smooth is not None):
        raise ValueError("you must specify either n or smooth")
    if smooth is not None:
        if smooth < 0.0 or smooth > 1.0:
            raise ValueError(f"smooth must be in [0, 1], but got {smooth}")
        n = int((1.0 - smooth) * 1 + smooth * len(values))
    ret = np.cumsum(values, dtype=float)
    ret[n:] = (ret[n:] - ret[:-n]) / n
    ret[:n] = ret[:n] / np.arange(1, n + 1)
    return ret


def exponential_moving_average(values, smooth: float) -> np.ndarray:
    if smooth < 0.0 or smooth > 1.0:
        raise ValueError(f"smooth must be in [0, 1], but got {smooth}")
    ema = np.zeros_like(values)
    ema[0] = values[0]
    for i in range(1, len(values)):
        ema[i] = smooth * values[i] + (1.0 - smooth) * ema[i - 1]
    return ema


class SyncFixedBuffer(Generic[T]):
    def __init__(self, max_size: int, callback: Optional[Callable[[Iterable[T]], None]] = None):
        self._max_size = max_size
        self._buffer: list[Optional[T]] = [None for _ in range(self._max_size)]
        self._updated = [False for _ in range(self._max_size)]
        self._sync_count = 0
        self._callback = callback

    @property
    def sync_done(self) -> bool:
        return self._sync_count == self._max_size

    def __len__(self):
        return len(self._buffer)

    def __getitem__(self, index) -> Optional[T]:
        return self._buffer[index]

    def __setitem__(self, index, value: T):
        self._buffer[index] = value  # type: ignore
        if not self._updated[index]:
            self._updated[index] = True
            self._sync_count += 1
        if self._callback is not None and self.sync_done:
            self._callback(tuple(self._buffer))  # type: ignore

    def __iter__(self):
        return iter(self._buffer)


class CSVSyncWriter:
    """
    Write a csv file with key and value fields. The key fields are used to identify the data.
    """

    def __init__(
            self,
            file_path: str,
            key_fields: Iterable[str],
            value_fields: Iterable[str],
    ) -> None:
        self._key_fields = tuple(key_fields)
        self._value_fields = tuple(value_fields)
        self._check_fields_unique()
        self._value_buffer = defaultdict(dict)
        self._field_types = {}

        self._file_path = file_path
        try:
            with open(self._file_path, "r") as f:
                reader = csv.DictReader(f)
                if reader.fieldnames is None:
                    raise ValueError
                if len(reader.fieldnames) != len(self.fields):
                    raise FileExistsError(
                        f"The number of fields in the csv file is different from the number of fields in the config. Create a new csv file.")
        except (FileNotFoundError, ValueError):
            # if the file does not exist or the file has no header, create a new csv file
            self._reset_csv()

    def add(self, keys: Union[tuple, dict], values: dict):
        """
        Add a new data to the csv file. If the data has all required values, write it to the csv file.
        
        Args:
            keys (tuple | dict): keys of the data. You must specify all keys.
            values (dict): values of the data. It automatically extracts required values from the `values` dict.
        """
        if len(keys) != len(self._key_fields):
            raise ValueError(f"keys must have {len(self._key_fields)} elements, but got {len(keys)}")
        if isinstance(keys, dict):
            keys = tuple(keys[key_field] for key_field in self._key_fields)
        # update the buffer with the new data only if values fields is in value_fields
        self._value_buffer[keys].update(dict_from_keys(values, self._value_fields))
        # check if it has all required values for these keys
        if len(self._value_buffer[keys]) == len(self._value_fields):
            if len(self._field_types) != len(self.fields):
                key_field_types = {key_field: type(key) for key_field, key in zip(self._key_fields, keys)}
                value_field_types = {value_field: type(value) for value_field, value in self._value_buffer[keys].items()
                                     if value is not None}
                self._field_types.update(key_field_types)
                self._field_types.update(value_field_types)
            self._write_csv(keys)
            # remove the keys from the buffer
            del self._value_buffer[keys]

    @property
    def key_fields(self) -> tuple[str, ...]:
        return self._key_fields

    @property
    def value_fields(self) -> tuple[str, ...]:
        return self._value_fields

    @value_fields.setter
    def value_fields(self, value: Iterable[str]):
        # update the value fields
        self._value_fields = tuple(value)
        self._check_fields_unique()
        # update the buffer from the old csv file
        with open(self._file_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                keys = tuple(self._field_types[key_field](row[key_field]) for key_field in self._key_fields)
                raw_value_dict = dict_from_keys(row, self._value_fields)
                # type conversion
                value_dict = {}
                for value_field, raw_value in raw_value_dict.items():
                    if raw_value is None or raw_value == "":
                        value_dict[value_field] = raw_value
                    else:
                        value_dict[value_field] = self._field_types[value_field](raw_value)
                self._value_buffer[keys] = value_dict
        self._reset_csv()

    @property
    def fields(self) -> tuple[str, ...]:
        return self.key_fields + self.value_fields

    def _check_fields_unique(self):
        if len(self.fields) != len(set(self.fields)):
            raise ValueError(f"all key and value fields must be unique")

    def _write_csv(self, keys: tuple):
        with open(self._file_path, "a") as f:
            writer = csv.DictWriter(f, fieldnames=self.fields)
            writer.writerow({**dict(zip(self._key_fields, keys)), **self._value_buffer[keys]})

    def _reset_csv(self):
        with open(self._file_path, "w") as f:
            writer = csv.DictWriter(f, fieldnames=self.fields)
            writer.writeheader()


def draw_molecules(smiles_list: list[str], scores: Optional[list[float]] = None, mols_per_row: int = 5,
                   title: str = ""):
    molecules = [Chem.MolFromSmiles(smiles) for smiles in smiles_list]
    labels = [f"SMILES: {smiles}" for smiles in smiles_list]
    if scores is not None:
        if len(molecules) != len(scores):
            raise ValueError(
                f"The number of molecules and scores must be the same, but got {len(molecules)} and {len(scores)}")
        labels = [f"{label}\nScore: {score}" for label, score in zip(labels, scores)]
    try:
        return Draw.MolsToGridImage(molecules, molsPerRow=mols_per_row, subImgSize=(500, 500), legends=labels)
    except ImportError:
        raise ImportError(
            "You cannot draw molecules due to the lack of libXrender.so.1. Install it with `sudo apt-get install libxrender1` or `conda install -c conda-forge libxrender`.")


def save_vocab(vocab: list[str], max_str_len: int, file_path: str):
    with open(file_path, "w") as f:
        json.dump({"vocabulary": vocab, "max_str_len": max_str_len}, f, indent=4)


def load_vocab(file_path: str) -> tuple[list[str], int]:
    with open(file_path, "r") as f:
        data = json.load(f)
    return data["vocabulary"], data["max_str_len"]


def save_smiles_or_selfies(smiles_or_selfies_list: list[str], file_path: str):
    with open(file_path, "w") as f:
        for smiles_or_selfies in smiles_or_selfies_list:
            f.write(f"{smiles_or_selfies}\n")


def load_smiles_or_selfies(file_path: str) -> list[str]:
    with open(file_path, "r") as f:
        return f.read().splitlines()


def to_selfies(smiles_or_selfies_list: list[str], verbose: bool = True) -> list[str]:
    if smiles_or_selfies_list[0].count("[") > 0:
        return smiles_or_selfies_list

    smiles_or_selfies_iter = tqdm(smiles_or_selfies_list,
                                  desc="Converting SMILES to SELFIES") if verbose else smiles_or_selfies_list
    selfies_list = [sf.encoder(s) for s in smiles_or_selfies_iter]
    return selfies_list


def to_smiles(smiles_or_selfies_list: list[str], verbose: bool = True) -> list[str]:
    if smiles_or_selfies_list[0].count("[") == 0:
        return smiles_or_selfies_list

    smiles_or_selfies_iter = tqdm(smiles_or_selfies_list,
                                  desc="Converting SELFIES to SMILES") if verbose else smiles_or_selfies_list
    smiles_list = [sf.decoder(s) for s in smiles_or_selfies_iter]
    return smiles_list  # type: ignore


@contextmanager
def suppress_print():
    original_stdout = sys.stdout  # Save original stdout
    original_stderr = sys.stderr  # Save original stderr
    sys.stdout = open(os.devnull, 'w')  # Redirect stdout to /dev/null
    sys.stderr = open(os.devnull, 'w')  # Redirect stderr to /dev/null
    try:
        yield
    finally:
        sys.stdout.close()  # Close redirected stdout
        sys.stderr.close()  # Close redirected stderr
        sys.stdout = original_stdout  # Restore original stdout
        sys.stderr = original_stderr  # Restore original stderr
