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
    origin: starlette.datastructures.URL
    target: str
    path: bool = False
    permanent: bool = False

    @classmethod
    def create(cls, txt_record: str, origin: starlette.datastructures.URL) -> Self:
        data = {"origin": origin}

        for attr in txt_record.split(";"):
            try:
                key, value = attr.split("=")
            except ValueError:
                raise HTTPException(
                    status_code=400, detail=f"Invalid TXT record: {txt_record}"
                )

            data[key] = value

        try:
            return TypeAdapter(Courier).validate_python(data)
        except ValidationError:
            raise HTTPException(
                status_code=400, detail=f"Invalid TXT record: {txt_record}"
            )

    @property
    def status_code(self) -> int:
        return 308 if self.permanent else 307

    @property
    def redirect(self) -> RedirectResponse:
        destination = URL(self.target)

        if self.path:
            destination = destination / self.origin.path.lstrip("/")

        return RedirectResponse(destination, status_code=self.status_code)


class NotFoundMessage(BaseModel):
    text: str
    secret: str = None
    ref: str

    # noinspection PyMethodParameters
    @model_validator(mode="after")
    def post_init(cls, message: Self) -> Self:
        terminus = message.text[-1]
        message.secret = terminus if terminus in string.punctuation else "."
        message.text = message.text.rstrip(string.punctuation)
        return message

    @classmethod
    def get(cls) -> Self:
        messages = toml.load((HERE / "messages.toml").open())["messages"]
        return TypeAdapter(NotFoundMessage).validate_python(random.choice(messages))


@app.api_route("/{_:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def resolver(request: Request):
    host = request.headers.get("host", "")

    if host == "courier.celsiusnarhwal.dev":
        return RedirectResponse(
            url="https://github.com/celsiusnarhwal/courier", status_code=308
        )

    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://dns.google/resolve",
            params={"name": f"courier_{host}", "type": "TXT"},
        ) as resp:
            resp.raise_for_status()
            dns = await resp.json()

    if dns.get("Status") != 0:
        raise HTTPException(404, detail="No Courier TXT record found")

    courier_record = next(
        (record for record in dns.get("Answer", []) if record), {}
    ).get("data")

    if not courier_record:
        raise HTTPException(404, detail="No Courier TXT record found")

    courier = Courier.create(courier_record, request.url)

    return courier.redirect


@app.exception_handler(404)
@app.exception_handler(400)
async def not_found(request: Request, _):
    return templates.TemplateResponse(
        "404.html",
        {
            "request": request,
            "message": NotFoundMessage.get(),
        },
    )
