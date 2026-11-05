"""Check the checked-in Power BI sources without Power BI Desktop or local backups.

Run from any directory: python path/to/scripts/validate_powerbi.py
This validates source structure and the project's known DAX definitions; it does
not execute DAX, refresh CSV files, or replace a visual review in Power BI.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "powerbi/project"
REPORT = PROJECT / "Olist.Report/definition"
MODEL = PROJECT / "Olist.SemanticModel/definition"
EXPECTED_SOURCES = {
    "a_monthly": "reports/tables/a_monthly.csv",
    "a_repeat90": "reports/tables/a_repeat90.csv",
    "service_orders": "data/powerbi/service_orders.csv",
    "service_calendar": "data/powerbi/service_calendar.csv",
}
IDENTIFIER = r"'(?:[^']|'')*'|[^\s.]+"

# Checking the supporting measures also prevents a ratio from silently becoming
# an average of previously calculated percentages.
EXPECTED_MEASURES = {
    "a_monthly": {
        "GMV": "SUM(a_monthly[delivered_gmv])",
        "Доставленные заказы": "SUM(a_monthly[delivered_orders])",
        "AOV": "DIVIDE([GMV], [Доставленные заказы])",
    },
    "a_repeat90": {
        "Покупатели с окном 90 дней": "SUM(a_repeat90[eligible_buyers])",
        "Повторные покупатели": "SUM(a_repeat90[repeat_buyers])",
        "Доля повторных покупок":
            "DIVIDE([Повторные покупатели], [Покупатели с окном 90 дней])",
        "Доля повторных покупок после суток":
            "DIVIDE(SUM(a_repeat90[repeat_after_1d_buyers]), [Покупатели с окном 90 дней])",
    },
    "service_orders": {
        "Доставленные заказы сервиса": "COUNTROWS(service_orders)",
        "Заказы с известной своевременностью": "COUNT(service_orders[is_late])",
        "Опоздавшие заказы": "SUM(service_orders[is_late])",
        "Доля опозданий":
            "DIVIDE([Опоздавшие заказы], [Заказы с известной своевременностью])",
        "Заказы с отзывом": "COUNT(service_orders[review_score])",
        "Низкие оценки": "SUM(service_orders[is_low_score])",
        "Доля низких оценок": "DIVIDE([Низкие оценки], [Заказы с отзывом])",
        "Отзывы после доставки":
            "CALCULATE([Заказы с отзывом], service_orders[review_after_delivery] = 1)",
        "Доля низких оценок после доставки":
            "CALCULATE([Доля низких оценок], service_orders[review_after_delivery] = 1)",
    },
}


def relative(path):
    return path.relative_to(ROOT).as_posix()


def require(condition, message, errors):
    if not condition:
        errors.append(message)
    return bool(condition)


def reject_json_constant(value):
    raise ValueError(f"Недопустимое JSON-значение {value}")


def read_json_sources(errors):
    paths = sorted(path for path in PROJECT.rglob("*")
                   if path.is_file() and ".pbi" not in path.parts
                   and path.suffix in {".json", ".pbip", ".pbir", ".pbism"})
    require(bool(paths), "Исходники JSON/PBIP/PBIR/PBISM не найдены", errors)
    documents = {}
    for path in paths:
        try:
            documents[path] = json.loads(path.read_text(encoding="utf-8-sig"),
                                         parse_constant=reject_json_constant)
        except (OSError, ValueError) as exc:
            errors.append(f"{relative(path)}: {exc}")
    return documents, len(paths)


def validate_layout(documents, errors):
    page_paths = sorted((REPORT / "pages").glob("*/page.json"))
    require(len(page_paths) == 3, f"Ожидались 3 страницы, найдено {len(page_paths)}", errors)
    metadata = documents.get(REPORT / "pages/pages.json", {})
    actual_ids = {path.parent.name for path in page_paths}
    order = metadata.get("pageOrder", [])
    require(len(order) == len(set(order)) and set(order) == actual_ids,
            "pageOrder должен включать каждую страницу ровно один раз", errors)
    pages, total = [], 0
    for path in page_paths:
        page = documents.get(path)
        if not isinstance(page, dict):
            errors.append(f"{relative(path)}: отсутствует объект страницы")
            continue
        label = page.get("displayName", path.parent.name)
        require(page.get("width") == 1920 and page.get("height") == 1080,
                f"{label}: размер страницы должен быть 1920 × 1080", errors)
        require(page.get("displayOption") == "FitToPage",
                f"{label}: ожидается FitToPage", errors)
        require(page.get("name") == path.parent.name,
                f"{label}: name не совпадает с каталогом страницы", errors)
        visual_paths = sorted((path.parent / "visuals").glob("*/visual.json"))
        total += len(visual_paths)
        positions, outside, overlaps = [], [], []
        for visual_path in visual_paths:
            visual = documents.get(visual_path)
            if not isinstance(visual, dict):
                errors.append(f"{relative(visual_path)}: отсутствует объект визуала")
                continue
            name = visual.get("name", visual_path.parent.name)
            require(name == visual_path.parent.name,
                    f"{label}: name визуала {name} не совпадает с каталогом", errors)
            position = visual.get("position", {})
            valid = all(type(position.get(key)) in (int, float)
                        and math.isfinite(position[key])
                        for key in ("x", "y", "width", "height"))
            if not require(valid, f"{label}/{name}: некорректные координаты", errors):
                continue
            x, y, width, height = (position[key] for key in ("x", "y", "width", "height"))
            require(width > 0 and height > 0,
                    f"{label}/{name}: размеры должны быть положительными", errors)
            inside = x >= 32 and y >= 24 and x + width <= 1888 and y + height <= 1048
            if not require(inside, f"{label}/{name}: визуал выходит за поля страницы", errors):
                outside.append(name)
            positions.append((name, x, y, x + width, y + height))
        for first, second in itertools.combinations(positions, 2):
            _, ax, ay, aright, abottom = first
            _, bx, by, bright, bbottom = second
            if min(aright, bright) > max(ax, bx) + 1e-6 and min(abottom, bbottom) > max(ay, by) + 1e-6:
                pair = [first[0], second[0]]
                overlaps.append(pair)
                errors.append(f"{label}: пересекаются визуалы {pair[0]} и {pair[1]}")
        pages.append({"page": label, "visual_count": len(visual_paths),
                      "out_of_bounds": outside, "overlaps": overlaps})
    require(total == 24, f"Ожидались 24 визуала, найдено {total}", errors)
    return pages, total


def object_blocks(text, kind):
    """Read declaration blocks at their indentation; references use ':' instead."""
    lines = text.splitlines()
    pattern = re.compile(rf"^(?P<indent>[ \t]*){kind}\s+(?P<header>.+)$")
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if not match:
            continue
        depth = len(match["indent"].expandtabs(4))
        body = []
        for following in lines[index + 1:]:
            stripped = following.lstrip()
            if stripped and len(following[:len(following) - len(stripped)].expandtabs(4)) <= depth:
                break
            body.append(following)
        yield match["header"], body


def unquote(value):
    return value[1:-1].replace("''", "'") if value.startswith("'") and value.endswith("'") else value


def endpoint(value):
    match = re.fullmatch(rf"({IDENTIFIER})\.({IDENTIFIER})", value)
    if match is None:
        raise ValueError(f"Некорректная ссылка на столбец: {value!r}")
    return unquote(match[1]), unquote(match[2])


def validate_relationships(tmdl, errors):
    relationships = []
    for path, text in tmdl.items():
        for name, body in object_blocks(text, "relationship"):
            properties = dict(re.findall(r"^\s*(\w+):\s*(.+?)\s*$", "\n".join(body), re.MULTILINE))
            try:
                start = endpoint(properties["fromColumn"])
                end = endpoint(properties["toColumn"])
            except (KeyError, ValueError) as exc:
                errors.append(f"{relative(path)}/{name}: {exc}")
                continue
            relationships.append((name, start, end, properties))
    require(len({value[0] for value in relationships}) == len(relationships),
            "Найдены повторные объявления связей", errors)
    auto = lambda value: value.startswith(("LocalDateTable_", "DateTableTemplate_"))
    business = [value for value in relationships if not auto(value[1][0]) and not auto(value[2][0])]
    require(len(business) == 1, f"Ожидалась 1 бизнес-связь, найдено {len(business)}", errors)
    summaries = []
    for name, start, end, properties in business:
        # TOM defaults omitted by TMDL: many -> one, active, oneDirection.
        # OneDirection filters from To (calendar) to From (orders):
        # https://learn.microsoft.com/en-us/dotnet/api/microsoft.analysisservices.tabular.singlecolumnrelationship
        valid = (start == ("service_orders", "purchase_date")
                 and end == ("service_calendar", "date")
                 and properties.get("fromCardinality", "many").casefold() == "many"
                 and properties.get("toCardinality", "one").casefold() == "one"
                 and properties.get("crossFilteringBehavior", "oneDirection").casefold() == "onedirection"
                 and properties.get("isActive", "true").casefold() == "true")
        require(valid, f"Связь {name}: ожидается активная service_calendar.date 1:* "
                "service_orders.purchase_date с фильтрацией от календаря к заказам", errors)
        summaries.append({"name": name, "from_column": ".".join(start),
                          "to_column": ".".join(end),
                          "from_cardinality": properties.get("fromCardinality", "many"),
                          "to_cardinality": properties.get("toCardinality", "one"),
                          "cross_filtering": properties.get("crossFilteringBehavior", "oneDirection"),
                          "active": properties.get("isActive", "true")})
    return {"business": summaries, "automatic_date_relationships": len(relationships) - len(business)}


def validate_csv_sources(tmdl, errors):
    parameter = tmdl.get(MODEL / "expressions.tmdl", "")
    require(bool(re.search(r"^expression\s+ProjectRoot\s*=", parameter, re.MULTILINE))
            and "IsParameterQuery=true" in parameter,
            "В expressions.tmdl должен быть параметр ProjectRoot", errors)
    sources = []
    for path, text in tmdl.items():
        calls = list(re.finditer(r"File\s*\.\s*Contents\s*\(\s*([^)]*)\)", text, re.IGNORECASE))
        call_count = len(re.findall(r"File\s*\.\s*Contents\s*\(", text, re.IGNORECASE))
        require(len(calls) == call_count, f"{relative(path)}: не удалось разобрать File.Contents", errors)
        for call in calls:
            match = re.fullmatch(r'ProjectRoot\s*&\s*"((?:[^"]|"")*)"\s*', call[1])
            if not require(match is not None,
                           f"{relative(path)}: File.Contents должен использовать ProjectRoot и относительный путь", errors):
                continue
            suffix = match[1].replace('""', '"').replace("\\", "/").lstrip("/")
            require(suffix == EXPECTED_SOURCES.get(path.stem),
                    f"{relative(path)}: неожиданный CSV-источник {suffix!r}", errors)
            sources.append({"table": path.stem, "path": suffix, "parameter": "ProjectRoot"})
    require(len(sources) == 4 and {value["table"] for value in sources} == set(EXPECTED_SOURCES),
            "Ожидались ровно 4 CSV-источника: a_monthly, a_repeat90, service_orders, service_calendar", errors)
    return sources


def normalize_dax(expression):
    expression = re.sub(r"'([A-Za-z_]\w*)'", r"\1", expression)
    return re.sub(r"\s+", "", expression).casefold()


def validate_measures(tmdl, errors):
    checked = []
    for table, expected in EXPECTED_MEASURES.items():
        path = MODEL / "tables" / f"{table}.tmdl"
        measures = {}
        for header, body in object_blocks(tmdl.get(path, ""), "measure"):
            match = re.fullmatch(r"('(?:[^']|'')*'|[^=]+?)\s*=\s*(.*)", header)
            if not require(match is not None, f"{relative(path)}: некорректное объявление меры", errors):
                continue
            expression = [match[2]]
            for line in body:
                if re.match(r"\s*(?:\w+:|annotation\b|isHidden\b)", line):
                    break
                expression.append(line.strip())
            name = unquote(match[1].strip())
            require(name not in measures, f"{table}: мера {name!r} объявлена повторно", errors)
            measures[name] = "\n".join(expression).strip()
        for name, formula in expected.items():
            actual = measures.get(name)
            valid = actual is not None and normalize_dax(actual) == normalize_dax(formula)
            require(valid, f"{table}/{name}: ожидалась формула {formula!r}, получена {actual!r}", errors)
            checked.append({"table": table, "measure": name, "passed": valid})
    return checked


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("powerbi/design_validation.json"),
                        help="JSON-отчёт; относительный путь отсчитывается от корня проекта")
    args = parser.parse_args()
    errors = []
    documents, json_count = read_json_sources(errors)
    pages, visual_count = validate_layout(documents, errors)
    tmdl_paths = sorted(MODEL.rglob("*.tmdl"))
    require(bool(tmdl_paths), "Исходники TMDL не найдены", errors)
    tmdl = {path: path.read_text(encoding="utf-8-sig") for path in tmdl_paths}
    relationships = validate_relationships(tmdl, errors)
    sources = validate_csv_sources(tmdl, errors)
    measures = validate_measures(tmdl, errors)
    result = {"passed": not errors,
              "scope": "Static PBIR/TMDL source checks; no UI, refresh or DAX execution",
              "json_files_checked": json_count, "tmdl_files_read": len(tmdl),
              "canvas": {"width": 1920, "height": 1080, "display_option": "FitToPage",
                         "margins": {"left": 32, "top": 24, "right": 32, "bottom": 32}},
              "pages": pages, "visual_count": visual_count,
              "relationships": relationships, "csv_sources": sources,
              "measures_checked": measures, "errors": errors}
    destination = args.output if args.output.is_absolute() else ROOT / args.output
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Power BI: {len(pages)} страниц, {visual_count} визуалов, "
          f"{json_count} JSON-файлов, {len(measures)} мер; ошибок: {len(errors)}")
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
