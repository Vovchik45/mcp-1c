"""Каталог в incoming: отпечаток, скан, разбор и повтор."""
import zipfile
from pathlib import Path

from conftest import (
    build_configuration,
    modules_configuration_xml,
    состарить,
    write_export,
    живой_клиент,
)
from starlette.applications import Starlette

from mcp1c import dashboard_backend as dashboard
from mcp1c.dashboard_runtime import DASHBOARD_ON, routes as spa_routes
from mcp1c.incoming import STATE_FAILED, STATE_NEW, STATE_READY, IncomingScanner
from mcp1c.intake import (
    INDEX_RESERVE,
    DumpLabels,
    configuration_labels,
    dump_labels,
    extract,
    identity_digest,
    identity_files,
    listing_size,
    parent_configuration_name,
    planned_size,
)
from mcp1c.registry import Registry


def _каталог_выгрузки(
    корень: Path,
    *,
    dump_info: str | None = "dump-1",
    version: str | None = None,
    name: str = "Конфигурация",
    config_version: str = "",
    модуль: str = "Процедура А() КонецПроцедуры",
) -> Path:
    корень.mkdir(parents=True, exist_ok=True)
    (корень / "Configuration.xml").write_text(
        modules_configuration_xml(name=name, version=config_version),
        encoding="utf-8",
    )
    (корень / "Catalogs/Т/Ext").mkdir(parents=True, exist_ok=True)
    (корень / "Catalogs/Т/Ext/ObjectModule.bsl").write_text(модуль, encoding="utf-8")
    (корень / "Ext/ParentConfigurations").mkdir(parents=True, exist_ok=True)
    (корень / "Ext/ParentConfigurations/П.cf").write_bytes(b"C" * 100)
    if dump_info is not None:
        (корень / "ConfigDumpInfo.xml").write_text(dump_info, encoding="utf-8")
    if version is not None:
        (корень / "VERSION").write_text(version, encoding="utf-8")
    for путь in корень.rglob("*"):
        if путь.is_file():
            состарить(путь)
    return корень


def test_extract_каталога_кладёт_только_отобранное(tmp_path):
    выгрузка = _каталог_выгрузки(tmp_path / "dump")
    корень = tmp_path / "modules"

    файлов, байт = extract(выгрузка, корень)
    нужно, формат = planned_size(выгрузка)

    assert формат == "tree"
    assert файлов == 1
    assert (корень / "Catalogs/Т/Ext/ObjectModule.bsl").read_text(
        encoding="utf-8"
    ) == "Процедура А() КонецПроцедуры"
    assert not (корень / "Ext").exists()
    assert not (корень / "Configuration.xml").exists()
    assert нужно == байт + INDEX_RESERVE


def test_обёртка_каталога_снимается_как_у_zip(tmp_path):
    внутренний = _каталог_выгрузки(tmp_path / "dump" / "Розница")
    корень = tmp_path / "modules"

    файлов, _ = extract(внутренний.parent, корень)

    assert файлов == 1
    assert (корень / "Catalogs/Т/Ext/ObjectModule.bsl").is_file()
    assert not (корень / "Розница").exists()


def test_отпечаток_каталога_из_config_dump_info(tmp_path):
    первый = _каталог_выгрузки(tmp_path / "a", dump_info="alpha")
    второй = _каталог_выгрузки(tmp_path / "b", dump_info="beta")

    assert identity_digest(первый) != identity_digest(второй)
    assert [p.name for p in identity_files(первый)] == ["ConfigDumpInfo.xml"]


def test_без_dump_info_хешируется_configuration_xml(tmp_path):
    каталог = _каталог_выгрузки(tmp_path / "dump", dump_info=None)
    другой = _каталог_выгрузки(tmp_path / "other", dump_info=None)
    (другой / "Configuration.xml").write_text(
        modules_configuration_xml(name="Другая"), encoding="utf-8"
    )
    состарить(другой / "Configuration.xml")

    assert [p.name for p in identity_files(каталог)] == ["Configuration.xml"]
    assert identity_digest(каталог) != identity_digest(другой)


def test_version_gitsync_входит_в_отпечаток(tmp_path):
    без_версии = _каталог_выгрузки(tmp_path / "a", dump_info="same")
    с_версией = _каталог_выгрузки(tmp_path / "b", dump_info="same", version="1.2.3")

    assert [p.name for p in identity_files(с_версией)] == [
        "ConfigDumpInfo.xml",
        "VERSION",
    ]
    assert identity_digest(без_версии) != identity_digest(с_версией)


def test_gitsync_без_dump_info_хеширует_configuration_и_version(tmp_path):
    каталог = _каталог_выгрузки(
        tmp_path / "dump", dump_info=None, version="1.2.3"
    )
    другой = _каталог_выгрузки(
        tmp_path / "other", dump_info=None, version="1.2.4"
    )

    assert [p.name for p in identity_files(каталог)] == [
        "Configuration.xml",
        "VERSION",
    ]
    assert identity_digest(каталог) != identity_digest(другой)


def test_нет_манифеста_не_хешируется_по_дереву(tmp_path):
    пустой = tmp_path / "empty"
    пустой.mkdir()
    (пустой / "Catalogs").mkdir()
    (пустой / "VERSION").write_text("1.0", encoding="utf-8")

    assert identity_files(пустой) == ()


def test_размер_каталога_это_файлы_идентичности_а_не_дерево(tmp_path):
    каталог = _каталог_выгрузки(tmp_path / "dump", dump_info="dump-1")
    (каталог / "ballast.bin").write_bytes(b"X" * 2_000_000)

    dump_info = каталог / "ConfigDumpInfo.xml"
    assert listing_size(каталог) == dump_info.stat().st_size
    assert listing_size(каталог) < 2_000_000


def test_configuration_labels_из_каталога_zip_и_манифеста(tmp_path):
    каталог = _каталог_выгрузки(
        tmp_path / "dump", name="Автосалон6", config_version="6.1.24.13"
    )
    архив = tmp_path / "dump.zip"
    with zipfile.ZipFile(архив, "w") as zf:
        zf.write(каталог / "Configuration.xml", "Configuration.xml")
    структура = tmp_path / "СтруктураКонфигурации.zip"
    with zipfile.ZipFile(структура, "w") as zf:
        zf.writestr("manifest.json", '{"name":"Автосалон6"}')

    assert configuration_labels(каталог) == ("Автосалон6", "6.1.24.13")
    assert configuration_labels(архив) == ("Автосалон6", "6.1.24.13")
    assert configuration_labels(структура) == ("", "")


def test_configuration_labels_каталога_не_обходит_дерево(tmp_path):
    каталог = _каталог_выгрузки(
        tmp_path / "dump", name="Автосалон6", config_version="6.1.24.13"
    )
    (каталог / "Documents" / "Заказ").mkdir(parents=True)
    (каталог / "Documents" / "Заказ" / "Configuration.xml").write_text(
        modules_configuration_xml(name="Чужой", version="9.9.9"),
        encoding="utf-8",
    )

    assert configuration_labels(каталог) == ("Автосалон6", "6.1.24.13")


def test_suggested_configuration_совпадает_по_name():
    names = ("Автосалон6", "Розница")

    assert dashboard.suggested_configuration("Автосалон6", names) == "Автосалон6"
    assert dashboard.suggested_configuration("AlisaIntegration", names) == ""
    assert dashboard.suggested_configuration("", ("Розница",)) == "Розница"


def test_incoming_path_принимает_zip_и_каталог(tmp_path):
    registry = Registry(tmp_path / "data")
    registry.incoming_dir.mkdir(parents=True)
    zip_path = registry.incoming_dir / "модули.zip"
    zip_path.write_bytes(b"PK")
    каталог = registry.incoming_dir / "Розница"
    каталог.mkdir()
    (каталог / "Configuration.xml").write_text("<a/>", encoding="utf-8")

    assert dashboard._incoming_path(registry, "модули.zip") == zip_path
    assert dashboard._incoming_path(registry, "Розница") == каталог
    assert dashboard._incoming_path(registry, "../secret") is None
    assert dashboard._incoming_path(registry, "нет-такого") is None
    assert dashboard._incoming_size(zip_path) == zip_path.stat().st_size
    assert dashboard._incoming_size(каталог) == listing_size(каталог)


def test_admin_payload_подставляет_родителя_и_подписи_xml():
    from types import SimpleNamespace

    from mcp1c.dashboard_backend import _IncomingRow
    from mcp1c.dashboard_runtime import _admin_sources_payload
    from mcp1c.incoming import STATE_NEW, STATE_READY

    prepared = SimpleNamespace(
        sources=SimpleNamespace(configuration_names=("Розница", "Автосалон6")),
        incoming=(
            _IncomingRow(
                name="Alisa",
                size=10,
                state=STATE_NEW,
                detail="",
                settling=False,
                kind="directory",
                export_name="AlisaIntegration",
                export_version="1.0.0.2",
                match_name="Розница",
            ),
            _IncomingRow(
                name="Розница",
                size=20,
                state=STATE_READY,
                detail="",
                settling=False,
                kind="directory",
                export_name="Розница",
                export_version="1.0",
                match_name="Розница",
            ),
        ),
        incoming_exists=True,
        incoming_dir="data/incoming/",
        jobs=(),
        orphans=(),
        sources_error="",
    )

    payload = _admin_sources_payload(prepared)
    новое, готово = payload["incoming"]

    assert новое["kind"] == "directory"
    assert новое["export_name"] == "AlisaIntegration"
    assert новое["export_version"] == "1.0.0.2"
    assert новое["suggested_configuration"] == "Розница"
    assert новое["action"] == "parse"
    assert готово["action"] == "reparse"
    assert готово["can_parse"] is True


def test_расширение_привязывается_по_имени_расширяемой_конфигурации(tmp_path):
    """`Name`/`Version` расширения — его собственные; родитель ищется по
    объектам, без сверки версий."""
    from conftest import extension_configuration_xml

    xml = extension_configuration_xml("AlisaIntegration").replace(
        "</Properties>",
        "<Version>1.0.0.2</Version></Properties>"
        "<ChildObjects><Catalog>Контрагенты</Catalog></ChildObjects>",
        1,
    )
    каталог = tmp_path / "cfe"
    каталог.mkdir()
    (каталог / "Configuration.xml").write_text(xml, encoding="utf-8")

    labels = dump_labels(каталог)
    assert labels.name == "AlisaIntegration"
    assert labels.version == "1.0.0.2"
    assert labels.extension is True
    assert ("Catalog", "Контрагенты") in labels.children

    names = ("Автосалон6", "Розница")
    objects = {
        "Автосалон6": {"Справочник.Автомобили"},
        "Розница": {"Справочник.Контрагенты", "Документ.РеализацияТоваровУслуг"},
    }
    assert parent_configuration_name(labels, names, objects) == "Розница"
    assert parent_configuration_name(
        DumpLabels("Автосалон6", "6.1.24.13"), names, objects
    ) == "Автосалон6"


def test_скан_видит_каталог_рядом_с_zip(tmp_path):
    registry = Registry(tmp_path / "data")
    registry.incoming_dir.mkdir(parents=True)
    zip_path = registry.incoming_dir / "модули.zip"
    zip_path.write_bytes(b"PK\x05\x06" + b"\0" * 18)
    состарить(zip_path)
    _каталог_выгрузки(registry.incoming_dir / "Розница", name="Розница")

    строки = IncomingScanner(registry).scan()
    имена = [row["name"] for row in строки]

    assert имена == ["Розница", "модули.zip"]
    по_имени = {row["name"]: row for row in строки}
    assert по_имени["Розница"]["kind"] == "directory"
    assert по_имени["модули.zip"]["kind"] == "archive"
    assert по_имени["Розница"]["state"] == STATE_NEW
    assert по_имени["модули.zip"]["state"] == STATE_NEW
    assert по_имени["Розница"]["export_name"] == "Розница"
    assert по_имени["модули.zip"]["export_name"] == ""


def test_вложенные_папки_выгрузки_не_отдельные_строки(tmp_path):
    registry = Registry(tmp_path / "data")
    registry.incoming_dir.mkdir(parents=True)
    _каталог_выгрузки(registry.incoming_dir / "Розница")

    имена = [row["name"] for row in IncomingScanner(registry).scan()]

    assert имена == ["Розница"]


def test_каталог_без_манифеста_показан_как_неудача(tmp_path):
    registry = Registry(tmp_path / "data")
    registry.incoming_dir.mkdir(parents=True)
    (registry.incoming_dir / "мусор").mkdir()
    состарить(registry.incoming_dir / "мусор")

    строки = IncomingScanner(registry).scan()

    assert строки[0]["state"] == STATE_FAILED
    assert "ConfigDumpInfo.xml" in строки[0]["detail"]
    assert "Configuration.xml" in строки[0]["detail"]


def test_add_modules_из_каталога_и_повтор_заменяет_код(tmp_path):
    входящее = tmp_path / "in"
    входящее.mkdir()
    registry = Registry(tmp_path / "data")
    registry.add_configuration(
        write_export(входящее, build_configuration(name="Розница"))
    )
    каталог = _каталог_выгрузки(tmp_path / "dump", dump_info="v1", модуль="Процедура Старая() КонецПроцедуры")

    первый = registry.add_modules(каталог, configuration="Розница")
    корень = registry.modules[первый.id].корень
    assert "Старая" in (корень / "Catalogs/Т/Ext/ObjectModule.bsl").read_text(
        encoding="utf-8"
    )

    (каталог / "Catalogs/Т/Ext/ObjectModule.bsl").write_text(
        "Процедура Новая() КонецПроцедуры", encoding="utf-8"
    )
    (каталог / "ConfigDumpInfo.xml").write_text("v2", encoding="utf-8")
    состарить(каталог / "ConfigDumpInfo.xml")
    состарить(каталог / "Catalogs/Т/Ext/ObjectModule.bsl")

    второй = registry.add_modules(каталог, configuration="Розница")
    assert второй.sha256 != первый.sha256
    assert "Новая" in (корень / "Catalogs/Т/Ext/ObjectModule.bsl").read_text(
        encoding="utf-8"
    )
    assert каталог.is_dir()


def test_spa_разбирает_каталог_и_повтор_даёт_202(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "admin-token")
    monkeypatch.delenv("API_TOKEN", raising=False)
    monkeypatch.setattr(dashboard, "_JOBS", [])
    входящее = tmp_path / "in"
    входящее.mkdir()
    registry = Registry(tmp_path / "data")
    registry.add_configuration(
        write_export(входящее, build_configuration(name="Розница"))
    )
    каталог = _каталог_выгрузки(registry.incoming_dir / "Розница")
    client = живой_клиент(
        Starlette(routes=spa_routes(registry, mode=DASHBOARD_ON))
    )
    client.post("/login", data={"token": "admin-token"})

    snapshot = client.get("/api/v1/sources/admin").json()
    row = next(item for item in snapshot["incoming"] if item["name"] == "Розница")
    assert row["kind"] == "directory"
    assert row["export_name"] == "Розница"
    assert row["suggested_configuration"] == "Розница"
    assert row["can_parse"] is True
    assert row["action"] == "parse"

    first = client.post(
        "/api/v1/sources/incoming/parse",
        json={"name": "Розница", "configuration": "Розница"},
    )
    assert first.status_code == 202
    _wait_until(lambda: "Розница:modules" in registry.snapshot().sources)

    ready = client.get("/api/v1/sources/admin").json()
    parsed = next(item for item in ready["incoming"] if item["name"] == "Розница")
    assert parsed["state"] == STATE_READY
    assert parsed["can_parse"] is True
    assert parsed["action"] == "reparse"

    second = client.post(
        "/api/v1/sources/incoming/parse",
        json={"name": "Розница", "configuration": "Розница"},
    )
    assert second.status_code == 202
    assert каталог.is_dir()


def _wait_until(условие, таймаут: float = 20.0) -> None:
    import time

    предел = time.monotonic() + таймаут
    while time.monotonic() < предел:
        if условие():
            return
        time.sleep(0.05)
    raise AssertionError("условие не выполнилось")
