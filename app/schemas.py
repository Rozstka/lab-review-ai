from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Measurement(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    name: Literal['Hemoglobin', 'Glucose', 'WBC']
    value: float = Field(ge=0, le=1000000)
    unit: str = Field(min_length=1, max_length=24)
    reference_low: float | None
    reference_high: float | None
    source_line: str = Field(min_length=1, max_length=500)

    @model_validator(mode='after')
    def ordered(self):
        if self.reference_low is not None and self.reference_high is not None and self.reference_low > self.reference_high:
            raise ValueError('Reference interval is reversed')
        return self

class Extraction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    measurements: list[Measurement] = Field(max_length=30)

class TextDocument(BaseModel):
    model_config = ConfigDict(extra='forbid')
    patient_id: str = Field(pattern=r'^SYN-[A-Za-z0-9-]{1,40}$')
    text: str = Field(min_length=1, max_length=20000)
    synthetic: Literal[True]

class Review(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=1)
    acknowledge_unparsed: bool = False
    decision: Literal['approve', 'reject']
    measurements: list[Measurement] | None = Field(default=None, max_length=30)
    note: str = Field(min_length=3, max_length=1000)
