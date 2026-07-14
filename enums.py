import enum


class Direction(str, enum.Enum):
    PARQUET_TO_CSV = "parquet_to_csv"
    CSV_TO_PARQUET = "csv_to_parquet"


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    ERROR = "error"