import os
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv()


class Settings(BaseSettings):
    # Gemini
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = "gemini-2.5-flash"

    # PostgresSQL
    # postgres_host: str = os.getenv("POSTGRES_HOST", "localhost")
    # postgres_port: int = int(os.getenv("POSTGRES_PORT", "5432"))
    # postgres_user: str = os.getenv("POSTGRES_USER", "postgres")
    # postgres_password: str = os.getenv("POSTGRES_PASSWORD", "")
    # postgres_db: str = os.getenv("POSTGRES_DB", "sql_testing_db")
    # test_db_template: str = os.getenv("TEST_DB_TEMPLATE", "test_template")
    #
    # # API
    # api_host: str = os.getenv("API_HOST", "0.0.0.0")
    # api_port: int = int(os.getenv("API_PORT", "8000"))
    #
    # @property
    # def database_url(self) -> str:
    #     return f"postgresql://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
    #
    # @property
    # def test_database_url_template(self) -> str:
    #     # used to create temporary test databases from a template
    #     return f"postgresql://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{}"
    #

settings = Settings()
