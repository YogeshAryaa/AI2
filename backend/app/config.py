from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- database
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "ragapp"

    # --- security
    admin_token: str
    app_secret_key: str
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    docs_enabled: bool = True             # set false in production to hide /docs
    max_upload_mb: int = 50
    default_key_rpm: int = 120            # default per-minute limit for new app API keys
    auth_fail_limit: int = 20             # wrong-token attempts per IP per minute before blocking

    # --- frontend (served by the backend when the build exists)
    serve_frontend: bool = True
    frontend_dist: str = "../frontend/dist"

    # --- embeddings
    # multilingual-e5-small understands English, Hindi and (reasonably) Hinglish.
    embedding_model: str = "intfloat/multilingual-e5-small"
    query_prefix: str = ""                # "" = automatic for known models, "none" = no prefix
    doc_prefix: str = ""
    embedding_backend: str = "torch"      # torch | onnx (onnx is ~2x faster on CPU, needs optimum[onnxruntime])
    embedding_batch_size: int = 64
    embedding_threads: int = 0            # 0 = let torch decide
    reranker_model: str = ""              # optional, e.g. cross-encoder/mmarco-mMiniLMv2-L12-H384-v1

    # --- vector search
    vector_backend: str = "local"         # local (in-process, any MongoDB) | atlas ($vectorSearch)
    vector_index_name: str = "vector_index"
    vector_dtype: str = "float32"         # float32 (fastest) | float16 (half the RAM, a bit slower)

    # --- answering defaults (changeable at runtime through PUT /settings)
    min_score: float = 0.5
    min_score_mode: str = "auto"          # auto = calibrated from your data | manual = use min_score
    top_k: int = 5
    cache_enabled: bool = True
    cache_threshold: float = 0.97
    cache_ttl_days: int = 14
    send_context_to_external: bool = True
    max_context_chars: int = 6000
    query_rewrite: bool = True            # retry a missed search with an AI-rewritten English query

    # --- chat history
    history_days: int = 0                 # 0 = keep forever

    # --- MongoDB auto-sync
    auto_sync_collections: str = ""
    auto_sync_minutes: int = 5
    sync_max_records: int = 0             # 0 = no limit

    # --- files the AI makes in the chat ("iski PDF bana do", "ek poster ki image banao")
    chat_files: bool = True               # false = treat such requests as normal questions
    # order to try: local (own AI on this server) | pollinations (free online) | openai (any OpenAI-compatible) | off
    image_provider: str = "local,pollinations"
    image_api_url: str = ""               # openai: base URL, e.g. https://api.openai.com/v1 (pollinations: optional)
    image_api_key: str = ""
    image_model: str = ""                 # e.g. flux (pollinations), gpt-image-1, black-forest-labs/FLUX.1-schnell
    image_size: str = "1024x1024"
    image_timeout_seconds: int = 120
    image_local_model: str = "stabilityai/sd-turbo"  # any diffusers text-to-image model
    image_local_steps: int = 2            # 1-4 for turbo models: more = a bit better, slower
    image_local_size: int = 512           # the model's own size; results are upscaled to IMAGE_SIZE
    pdf_color: str = "blue"               # default colour of PDFs made in the chat (see styles.COLORS)
    files_days: int = 0                   # delete generated files after N days (0 = keep)

    # --- Studio: read images / scanned PDFs on this server (OCR), no AI service
    ocr_languages: str = "en,hi"          # EasyOCR codes; Hindi must be paired with English only
    ocr_model_dir: str = ""               # where OCR models are kept ("" = ~/.EasyOCR)

    chunk_chars: int = 1000
    chunk_overlap: int = 150


cfg = Config()
