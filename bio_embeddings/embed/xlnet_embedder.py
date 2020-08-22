import logging
import re
import tempfile
from pathlib import Path
from typing import Optional, Generator, List

import torch
from numpy import ndarray
from transformers import XLNetModel, XLNetTokenizer

from bio_embeddings.embed.embedder_interfaces import EmbedderInterface
from bio_embeddings.utilities import get_model_directories_from_zip

logger = logging.getLogger(__name__)


class XLNetEmbedder(EmbedderInterface):
    name = "xlnet"
    embedding_dimension = 1024
    number_of_layers = 1
    _model: XLNetModel
    _model_fallback: Optional[XLNetModel]

    def __init__(self, **kwargs):
        """
        Initialize XLNet embedder.

        :param model_directory:
        :param use_cpu: overwrite autodiscovery and force CPU use
        """
        super().__init__(**kwargs)

        # Get file locations from kwargs
        self.model_directory = self._options.get("model_directory")

        self._model = (
            XLNetModel.from_pretrained(self.model_directory).to(self._device).eval()
        )
        self._model_fallback = None

        # sentence piece model
        # A standard text tokenizer which creates the input for NNs trained on text.
        # This one is just indexing single amino acids because we only have words of L=1.
        spm_model = str(Path(self.model_directory).joinpath("spm_model.model"))
        self._tokenizer = XLNetTokenizer.from_pretrained(spm_model, do_lower_case=False)

    def _get_fallback_model(self) -> XLNetModel:
        if not self._model_fallback:
            self._model_fallback = XLNetModel.from_pretrained(
                self.model_directory
            ).eval()
        return self._model_fallback

    @classmethod
    def with_download(cls, **kwargs):
        necessary_directories = ["model_directory"]

        keep_tempfiles_alive = []
        for directory in necessary_directories:
            if not kwargs.get(directory):
                f = tempfile.mkdtemp()
                keep_tempfiles_alive.append(f)

                get_model_directories_from_zip(
                    path=f, model=cls.name, directory=directory
                )

                kwargs[directory] = f
        return cls(**kwargs)

    def embed(self, sequence: str) -> ndarray:
        sequence_length = len(sequence)
        sequence = re.sub(r"[UZOBX]", "<unk>", sequence)

        # Tokenize sequence with spaces
        sequence = " ".join(list(sequence))

        # tokenize sequence
        tokenized_sequence = torch.tensor(
            [self._tokenizer.encode(sequence, add_special_tokens=True)]
        ).to(self._device)

        with torch.no_grad():
            try:
                embedding = self._model(tokenized_sequence)
            except RuntimeError:
                embedding = self._get_fallback_model()(tokenized_sequence)

            # drop batch dimension and remove special tokens added to end
            embedding = embedding[0].squeeze()[:-2]

        assert (
            sequence_length == embedding.shape[0]
        ), f"Sequence length mismatch: {sequence_length} vs {embedding.shape[0]}"

        return embedding.cpu().detach().numpy().squeeze()

    def embed_batch(self, batch: List[str]) -> Generator[ndarray, None, None]:
        # TODO: Actual batching for xlnet
        return (self.embed(sequence) for sequence in batch)

    @staticmethod
    def reduce_per_protein(embedding):
        return embedding.mean(axis=0)