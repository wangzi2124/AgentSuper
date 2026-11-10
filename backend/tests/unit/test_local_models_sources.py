from __future__ import annotations

import struct

from app.local_models import metadata, sources


def _gguf(meta: dict) -> bytes:
    chunks = [b"GGUF", struct.pack("<I", 3), struct.pack("<Q", 0), struct.pack("<Q", len(meta))]
    for key, value in meta.items():
        kb = key.encode("utf-8")
        chunks.append(struct.pack("<Q", len(kb)))
        chunks.append(kb)
        if isinstance(value, str):
            chunks.append(struct.pack("<I", 8))
            vb = value.encode("utf-8")
            chunks.append(struct.pack("<Q", len(vb)))
            chunks.append(vb)
        else:
            chunks.append(struct.pack("<I", 4))
            chunks.append(struct.pack("<I", int(value)))
    return b"".join(chunks)


def test_parse_gguf_metadata_roundtrip(tmp_path):
    path = tmp_path / "m.gguf"
    path.write_bytes(_gguf({
        "general.architecture": "qwen2",
        "general.name": "Qwen2.5 7B Instruct",
        "qwen2.context_length": 32768,
        "tokenizer.chat_template": "{% if tools %}{{ tools }}{% endif %}",
    }))
    meta = metadata.parse_gguf_metadata(path)
    assert meta["general.architecture"] == "qwen2"
    assert meta["qwen2.context_length"] == 32768
    assert "tools" in meta["tokenizer.chat_template"]


def test_parse_gguf_rejects_non_gguf(tmp_path):
    path = tmp_path / "x.bin"
    path.write_bytes(b"NOTGGUF" + b"\x00" * 64)
    assert metadata.parse_gguf_metadata(path) == {}


def test_build_catalog_entry_auto_params():
    entry = metadata.build_catalog_entry(
        repo_id="Qwen/Qwen2.5-7B-Instruct-GGUF",
        file="qwen2.5-7b-instruct-q4_k_m.gguf",
        source="huggingface",
        meta={
            "general.architecture": "qwen2",
            "general.name": "Qwen2.5 7B Instruct",
            "qwen2.context_length": 32768,
            "tokenizer.chat_template": "{% if tools %}{{ tools }}{% endif %}",
        },
    )
    assert entry["id"].startswith("ollama_chat/qwen2.5-7b-instruct-q4_k_m")
    assert entry["provider"] == "ollama"
    assert entry["context_length"] == 32768
    assert entry["capabilities"]["tool_use"] is True
    assert entry["limits"]["max_output_tokens"] <= entry["context_length"] // 4 or entry["limits"]["max_output_tokens"] <= 32000
    assert entry["local"]["quantization"] == "Q4_K_M"
    assert entry["cost"]["input_per_1m"] == 0.0


def test_ollama_name_strips_quant_suffix():
    name = metadata.ollama_model_name(
        "org/repo", "model-q8_0.gguf", {"general.name": "Some Model.Q8_0"}
    )
    assert name == "some-model-q8_0"


def test_ollama_name_avoids_generic_names():
    name = metadata.ollama_model_name(
        "acme/mistral-7b-gguf", "m-q4_k_m.gguf", {"general.name": "llama"}
    )
    assert name == "mistral-7b-gguf-q4_k_m"


def test_group_split_gguf(monkeypatch):
    def fake(url, method="GET", payload=None, timeout=20.0):
        return {"siblings": [
            {"rfilename": "m-q4_k_m.gguf", "size": 10},
            {"rfilename": "m-fp16-00001-of-00002.gguf", "size": 20},
            {"rfilename": "m-fp16-00002-of-00002.gguf", "size": 30},
            {"rfilename": "README.md", "size": 1},
        ]}

    monkeypatch.setattr(sources, "_request_json", fake)
    files = sources.list_gguf_files("org/m", "huggingface")
    by_name = {f["file"]: f for f in files}
    assert by_name["m-q4_k_m.gguf"]["split"] is False
    split = by_name["m-fp16-00001-of-00002.gguf"]
    assert split["split"] is True
    assert split["files"] == ["m-fp16-00001-of-00002.gguf", "m-fp16-00002-of-00002.gguf"]
    assert split["size"] == 50


def test_modelscope_search_parses_pascal_case(monkeypatch):
    def fake(url, method="GET", payload=None, timeout=20.0):
        assert method == "PUT"
        return {"Code": 200, "Data": {"Models": [
            {"Path": "Qwen", "Name": "Qwen2.5-7B-Instruct-GGUF", "ChineseName": "Qwen2.5", "Downloads": 5},
        ]}}

    monkeypatch.setattr(sources, "_request_json", fake)
    models = sources.search_models("qwen", "modelscope")
    assert models[0]["id"] == "Qwen/Qwen2.5-7B-Instruct-GGUF"
    assert models[0]["source"] == "modelscope"


def test_download_url_shapes(monkeypatch):
    monkeypatch.setattr(sources, "endpoints", lambda: {
        "modelscope": "https://ms.test",
        "huggingface": "https://hf.test",
    })
    assert sources.download_url("huggingface", "org/m", "a b.gguf") == "https://hf.test/org/m/resolve/main/a%20b.gguf"
    ms = sources.download_url("modelscope", "org/m", "x.gguf")
    assert ms.startswith("https://ms.test/api/v1/models/org/m/repo?")
    assert "FilePath=x.gguf" in ms


def test_quantization_and_parameter_heuristics():
    assert sources.quantization_of("Qwen2.5-7B-Instruct-IQ4_XS.gguf") == "IQ4_XS"
    assert sources.quantization_of("model-bf16.gguf") == "BF16"
    assert sources.quantization_of("plain.gguf") == ""
    assert sources.parameter_size_of("Qwen2.5-7B-Instruct") == 7.0
