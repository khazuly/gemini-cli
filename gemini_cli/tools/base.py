from abc import ABC, abstractmethod
from typing import Any


class Tool(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def parameters(self) -> dict: ...

    @abstractmethod
    def execute(self, args: dict[str, Any]) -> str: ...

    def to_prompt(self) -> str:
        return f"{self.name}: {self.description}\nParameters: {self.parameters}"
