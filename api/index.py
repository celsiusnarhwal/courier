import random
import string

import aiohttp
import starlette.datastructures
import tomlkit as toml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from path import Path
from pydantic import BaseModel, TypeAdapter, ValidationError, model_validator
from typing_extensions import Self
from yarl import URL

HERE = Path(__file__).parent

app = FastAPI()

app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

templates = Jinja2Templates(directory=HERE / "templates")


class Courier(BaseModel):
    target: str
    path: bool = False
    permanent: bool = False

    @classmethod
    def create(cls, txt_record: str) -> Self:
        data = {}

        for attr in txt_record.split(";"):
            try:
                key, value = attr.split("=")
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid TXT record")

            data[key] = value

        try:
            return TypeAdapter(Courier).validate_python(data)
        except ValidationError:
            raise HTTPException(status_code=400, detail="Invalid TXT record")

    @property
    def status_code(self):
        return 308 if self.permanent else 307

    def destination(self, origin: starlette.datastructures.URL):
        destination = URL(self.target)

        if self.path:
            destination = destination / origin.path.lstrip("/")

        return destination


class NotFoundMessage(BaseModel):
    text: str
    secret: str = None
    ref: str

    # noinspection PyMethodParameters
    @model_validator(mode="after")
    def validate(cls, obj: Self):
        terminus = obj.text[-1]
        obj.secret = terminus if terminus in string.punctuation else "."
        obj.text = obj.text.rstrip(string.punctuation)
        return obj

    @classmethod
    def get(cls) -> Self:
        messages = toml.load((HERE / "messages.toml").open())["messages"]
        return TypeAdapter(NotFoundMessage).validate_python(random.choice(messages))


def raise_404(internal: bool = False):
    raise HTTPException(404, headers={"x-internal": internal})


@app.api_route("/{_:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def resolver(request: Request, internal: bool = False):
    host = request.headers.get("host", "")
    internal = internal and host == "404.celsiusnarhwal.dev"

    if host == "courier.celsiusnarhwal.dev":
        return RedirectResponse(
            url="https://github.com/celsiusnarhwal/courier", status_code=308
        )

    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://dns.google/resolve",
            params={"name": f"_.{host}", "type": "TXT"},
        ) as resp:
            resp.raise_for_status()
            dns = await resp.json()

    if dns.get("Status") != 0:
        raise_404(internal)

    courier_record = next(
        (record for record in dns.get("Answer", []) if record), {}
    ).get("data")

    if not courier_record:
        raise_404(internal)

    courier = Courier.create(courier_record)

    return RedirectResponse(
        url=courier.destination(request.url), status_code=courier.status_code
    )


@app.exception_handler(404)
async def not_found(request: Request, _):
    return templates.TemplateResponse(
        "404.html",
        {
            "request": request,
            "message": NotFoundMessage.get(),
            "internal": request.headers.get("x-internal", False),
        },
    )
