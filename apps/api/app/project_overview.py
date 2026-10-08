from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tomllib
from typing import Any
from xml.etree import ElementTree

MAX_PROJECT_FILES = 10_000
IGNORED_PARTS = {
    ".git", "node_modules", "dist", "build", ".next", ".venv", "venv",
    "coverage", "vendor", "__pycache__", ".cache", ".agent-lab-baseline",
}
SENSITIVE_PARTS = {".aws", ".azure", ".gcloud", ".ssh", ".vercel", "secrets", "certificates"}
SENSITIVE_NAMES = {".npmrc", ".pypirc", ".netrc", "credentials", "service-account.json"}


def is_sensitive_project_file(relative: str) -> bool:
    parts = Path(relative).parts
    basename = parts[-1].casefold() if parts else ""
    return (
        any(part.casefold() in SENSITIVE_PARTS for part in parts[:-1])
        or basename.startswith(".env")
        or basename in SENSITIVE_NAMES
        or any(marker in basename for marker in ("secret", "credential", "token"))
        or basename.endswith((".pem", ".key", ".p12", ".pfx"))
    )

LANGUAGE_BY_EXTENSION = {
    ".py": "Python",
    ".pyi": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".kt": "Kotlin",
    ".go": "Go",
    ".rs": "Rust",
    ".cs": "C#",
    ".php": "PHP",
    ".rb": "Ruby",
    ".swift": "Swift",
    ".cpp": "C++",
    ".cc": "C++",
    ".c": "C",
    ".h": "C/C++",
    ".hpp": "C++",
    ".sh": "Shell",
    ".sql": "SQL",
    ".vue": "Vue",
    ".svelte": "Svelte",
}

FRAMEWORK_PACKAGES = {
    "next": "Next.js",
    "react": "React",
    "vite": "Vite",
    "express": "Express",
    "@nestjs/core": "NestJS",
    "vue": "Vue",
    "@angular/core": "Angular",
    "svelte": "Svelte",
    "astro": "Astro",
    "fastapi": "FastAPI",
    "django": "Django",
    "flask": "Flask",
    "spring-boot-starter-web": "Spring Boot",
    "org.springframework.boot": "Spring Boot",
    "laravel/framework": "Laravel",
    "symfony/framework-bundle": "Symfony",
}

TOOL_PACKAGES = {
    "tailwindcss": "Tailwind CSS",
    "prisma": "Prisma",
    "@prisma/client": "Prisma",
    "mongoose": "Mongoose",
    "pymongo": "MongoDB",
    "motor": "MongoDB",
    "psycopg": "PostgreSQL",
    "psycopg2": "PostgreSQL",
    "pg": "PostgreSQL",
    "mysql2": "MySQL",
    "sqlalchemy": "SQLAlchemy",
    "typeorm": "TypeORM",
    "sequelize": "Sequelize",
    "pytest": "pytest",
}

MANIFEST_NAMES = {
    "package.json", "requirements.txt", "pyproject.toml", "pom.xml",
    "build.gradle", "build.gradle.kts", "go.mod", "Cargo.toml",
    "composer.json", "Gemfile",
}

DATABASE_SIGNALS = {
    "mongodb": "MongoDB",
    "mongoose": "MongoDB",
    "pymongo": "MongoDB",
    "motor": "MongoDB",
    "pg": "PostgreSQL",
    "psycopg": "PostgreSQL",
    "psycopg2": "PostgreSQL",
    "postgres": "PostgreSQL",
    "mysql": "MySQL",
    "mysql2": "MySQL",
    "sqlite": "SQLite",
    "better-sqlite3": "SQLite",
    "sqlalchemy": "SQLAlchemy",
    "prisma": "Prisma ORM",
    "@prisma/client": "Prisma ORM",
    "typeorm": "TypeORM",
    "sequelize": "Sequelize",
    "drizzle-orm": "Drizzle ORM",
    "redis": "Redis",
}
AUTH_SIGNALS = {
    "next-auth": "NextAuth.js",
    "@auth/core": "Auth.js",
    "passport": "Passport.js",
    "jsonwebtoken": "JWT",
    "jose": "JOSE/JWT",
    "bcrypt": "Password hashing",
    "bcryptjs": "Password hashing",
    "@clerk/nextjs": "Clerk",
    "firebase-admin": "Firebase Auth",
    "firebase": "Firebase Auth",
    "auth0": "Auth0",
    "@supabase/supabase-js": "Supabase Auth",
}


def discover_project_files(root: Path) -> list[str]:
    paths: list[str] = []
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        directories[:] = [
            name for name in directories
            if name.lower() not in IGNORED_PARTS and not (current_path / name).is_symlink()
        ]
        for filename in filenames:
            file_path = current_path / filename
            if file_path.is_symlink():
                continue
            relative = file_path.relative_to(root).as_posix()
            if relative == "workspace.json":
                continue
            if any(part.lower() in IGNORED_PARTS for part in Path(relative).parts):
                continue
            if is_sensitive_project_file(relative):
                continue
            paths.append(relative)
            if len(paths) > MAX_PROJECT_FILES:
                return sorted(paths, key=str.casefold)[:MAX_PROJECT_FILES]
    return sorted(paths, key=str.casefold)


def _read_small_text(root: Path, relative: str, limit: int = 1024 * 1024) -> str:
    path = root / Path(relative)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _add_dependencies(found: set[str], dependencies: dict[str, Any]) -> None:
    normalized = {str(name).lower(): value for name, value in dependencies.items()}
    for package, label in FRAMEWORK_PACKAGES.items():
        if package in normalized:
            found.add(label)
    for package, label in TOOL_PACKAGES.items():
        if package in normalized:
            found.add(label)


def _collect_python_dependencies(root: Path, file_names: set[str]) -> set[str]:
    dependencies: dict[str, Any] = {}
    for requirements_path in (name for name in file_names if Path(name).name.lower() == "requirements.txt"):
        requirements = _read_small_text(root, requirements_path)
        for line in requirements.splitlines():
            match = re.match(r"\s*([A-Za-z0-9_.-]+)", line)
            if match and not line.lstrip().startswith(("#", "-")):
                dependencies[match.group(1).lower().replace("_", "-")] = ""
    for pyproject_path in (name for name in file_names if Path(name).name.lower() == "pyproject.toml"):
        try:
            config = tomllib.loads(_read_small_text(root, pyproject_path))
            project = config.get("project", {})
            for dependency in project.get("dependencies", []):
                match = re.match(r"\s*([A-Za-z0-9_.-]+)", dependency)
                if match:
                    dependencies[match.group(1).lower().replace("_", "-")] = ""
            tool = config.get("tool", {})
            poetry = tool.get("poetry", {}).get("dependencies", {})
            dependencies.update({str(key).lower().replace("_", "-"): value for key, value in poetry.items()})
        except (tomllib.TOMLDecodeError, TypeError, AttributeError):
            pass
    found: set[str] = set()
    _add_dependencies(found, dependencies)
    return found


def _collect_package_json(root: Path, file_names: set[str]) -> set[str]:
    dependencies: dict[str, Any] = {}
    for package_path in (name for name in file_names if Path(name).name.lower() == "package.json"):
        try:
            package = json.loads(_read_small_text(root, package_path))
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(package, dict):
            continue
        for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            section_dependencies = package.get(section, {})
            if isinstance(section_dependencies, dict):
                dependencies.update(section_dependencies)
    found: set[str] = set()
    _add_dependencies(found, dependencies)
    return found


def _collect_manifest_technologies(root: Path, file_names: set[str]) -> set[str]:
    technologies = _collect_package_json(root, file_names)
    technologies.update(_collect_python_dependencies(root, file_names))

    for go_mod_path in (name for name in file_names if Path(name).name.lower() == "go.mod"):
        text = _read_small_text(root, go_mod_path).lower()
        for marker, label in {
            "github.com/gin-gonic/gin": "Gin",
            "github.com/labstack/echo": "Echo",
            "github.com/go-chi/chi": "Chi",
            "gorm.io/gorm": "GORM",
        }.items():
            if marker in text:
                technologies.add(label)

    for cargo_path in (name for name in file_names if Path(name).name.lower() == "cargo.toml"):
        try:
            cargo = tomllib.loads(_read_small_text(root, cargo_path))
            dependencies = cargo.get("dependencies", {})
            _add_dependencies(technologies, dependencies)
            for package, label in {
                "tokio": "Tokio",
                "axum": "Axum",
                "actix-web": "Actix Web",
                "rocket": "Rocket",
                "sqlx": "SQLx",
            }.items():
                if package in dependencies:
                    technologies.add(label)
        except (tomllib.TOMLDecodeError, TypeError, AttributeError):
            pass

    for name in file_names:
        if name.lower().endswith(".csproj"):
            content = _read_small_text(root, name).lower()
            if "microsoft.aspnetcore" in content or "sdk.web" in content:
                technologies.add("ASP.NET Core")
            if "entityframeworkcore" in content:
                technologies.add("Entity Framework Core")
        elif name in {"pom.xml", "build.gradle", "build.gradle.kts"}:
            content = _read_small_text(root, name).lower()
            if "spring-boot" in content or "org.springframework.boot" in content:
                technologies.add("Spring Boot")

    return technologies


def _dependency_names(root: Path, file_names: set[str]) -> set[str]:
    names: set[str] = set()
    for relative in file_names:
        basename = Path(relative).name.casefold()
        if basename == "package.json":
            try:
                package = json.loads(_read_small_text(root, relative))
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(package, dict):
                for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
                    values = package.get(section, {})
                    if isinstance(values, dict):
                        names.update(str(name).casefold() for name in values)
        elif basename in {"requirements.txt", "go.mod", "cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts"}:
            for line in _read_small_text(root, relative).splitlines():
                names.update(token.casefold() for token in re.findall(r"[A-Za-z][A-Za-z0-9_.@/-]{2,}", line))
        elif basename == "pyproject.toml":
            try:
                config = tomllib.loads(_read_small_text(root, relative))
                for dependency in config.get("project", {}).get("dependencies", []):
                    match = re.match(r"\s*([A-Za-z0-9_.-]+)", dependency)
                    if match:
                        names.add(match.group(1).casefold().replace("_", "-"))
                poetry = config.get("tool", {}).get("poetry", {}).get("dependencies", {})
                if isinstance(poetry, dict):
                    names.update(str(name).casefold().replace("_", "-") for name in poetry)
            except (tomllib.TOMLDecodeError, TypeError, AttributeError):
                continue
    return names


def _evidence_files(file_names: set[str], markers: tuple[str, ...], limit: int = 12) -> list[str]:
    return sorted(
        (name for name in file_names if any(marker in name.casefold() for marker in markers)),
        key=str.casefold,
    )[:limit]


def _project_map(file_names: set[str]) -> list[dict[str, Any]]:
    groups = [
        ("Frontend", ("frontend", "client", "web", "ui", "components", "pages", "/app/")),
        ("Backend and API", ("backend", "server", "api", "routes", "controllers", "handlers")),
        ("Data and database", ("database", "db", "schema", "migration", "model", "repository", "prisma")),
        ("Authentication", ("auth", "login", "session", "permission", "jwt")),
        ("Tests", ("test", "spec", "__tests__")),
        ("Configuration and deployment", ("docker", "compose", "config", "\.github", "\.env.example", "\.yml", "\.yaml")),
    ]
    result = []
    for label, markers in groups:
        matches = sorted((
            path for path in file_names
            if any(
                marker in f"/{path.casefold()}/" if marker.startswith("/")
                else marker in path.casefold()
                for marker in markers
            )
        ), key=str.casefold)[:8]
        if matches:
            result.append({"name": label, "file_count": sum(
                1 for path in file_names if any(
                    marker in f"/{path.casefold()}/" if marker.startswith("/")
                    else marker in path.casefold()
                    for marker in markers
                )
            ), "evidence_files": matches})
    return result


def _source_api_frameworks(root: Path, file_names: set[str]) -> set[str]:
    signatures = {
        "FastAPI": ("from fastapi import", "fastapi()"),
        "Flask": ("from flask import", "flask(__name__)"),
        "Django": ("from django.urls import", "django.conf.urls"),
        "Express": ("express()", "require('express')", 'require("express")'),
        "NestJS": ("@nestjs/common", "@controller("),
        "Spring Boot": ("@springbootapplication", "spring-boot-starter-web"),
        "ASP.NET Core": ("microsoft.aspnetcore", "mapget(", "mappost("),
        "Laravel": ("illuminate\\support\\facades\\route", "laravel\\"),
    }
    found: set[str] = set()
    inspected = 0
    for relative in sorted(file_names, key=str.casefold):
        if Path(relative).suffix.casefold() not in {".py", ".js", ".jsx", ".ts", ".tsx", ".php", ".cs", ".java"}:
            continue
        content = _read_small_text(root, relative, limit=64 * 1024).casefold()
        inspected += 1
        for framework, needles in signatures.items():
            if any(needle in content for needle in needles):
                found.add(framework)
        if inspected >= 80:
            break
    return found


def analyze_project(root: Path, file_paths: list[str]) -> dict[str, Any]:
    languages: dict[str, int] = {}
    top_level: dict[str, str] = {}
    manifests: list[str] = []

    for relative in file_paths:
        relative_path = Path(relative)
        parts = relative_path.parts
        if not parts:
            continue
        root_name = parts[0]
        item_type = "folder" if len(parts) > 1 else "file"
        top_level[root_name] = item_type
        extension = relative_path.suffix.lower()
        language = LANGUAGE_BY_EXTENSION.get(extension)
        if language:
            languages[language] = languages.get(language, 0) + 1
        basename = relative_path.name.lower()
        if basename in MANIFEST_NAMES or basename.endswith(".csproj"):
            manifests.append(relative)

    technologies = _collect_manifest_technologies(root, set(file_paths))
    file_names = set(file_paths)
    dependencies = _dependency_names(root, file_names)
    if any(language in languages for language in ("JavaScript", "TypeScript")):
        technologies.add("Node.js")
    if "Python" in languages:
        technologies.add("Python")
    if "Go" in languages:
        technologies.add("Go")
    if "Rust" in languages:
        technologies.add("Rust")

    language_rows = [
        {"name": name, "files": count}
        for name, count in sorted(languages.items(), key=lambda item: (-item[1], item[0]))
    ]
    ordered_technologies = sorted(technologies)
    language_summary = ", ".join(f"{row['name']} ({row['files']})" for row in language_rows[:4])
    summary = f"Found {len(file_paths)} project files. "
    summary += f"Languages: {language_summary}. " if language_summary else "No source language was detected from file extensions. "
    summary += f"Manifest signals: {', '.join(ordered_technologies)}." if ordered_technologies else "No supported framework or library manifest was recognized."

    database_systems = sorted({label for package, label in DATABASE_SIGNALS.items() if package in dependencies})
    database_files = _evidence_files(file_names, ("schema", "migration", "model", "repository", ".sql", "prisma"))
    if database_systems:
        dependency_manifests = [
            name for name in file_names
            if Path(name).name.casefold() in MANIFEST_NAMES
            or Path(name).name.casefold() in {"go.mod", "cargo.toml"}
        ]
        database_files = sorted(set(database_files + dependency_manifests), key=str.casefold)[:12]
    if not database_systems and database_files:
        database_systems = ["Database-related files detected; engine not identified"]
    api_files = _evidence_files(file_names, ("/routes/", "/route.", "/controller", "/endpoint", "/handlers/", "/api/"))
    api_frameworks = sorted({name for name in ordered_technologies if name in {
        "FastAPI", "Flask", "Django", "Express", "NestJS", "Spring Boot", "ASP.NET Core", "Gin", "Echo", "Chi", "Laravel", "Symfony"
    }} | _source_api_frameworks(root, file_names))
    authentication_signals = sorted({label for package, label in AUTH_SIGNALS.items() if package in dependencies})
    authentication_files = [
        path for path in _evidence_files(file_names, ("auth", "login", "session", "permission", "jwt", "oauth"))
        if not any(part.casefold() in {"tests", "test", "__tests__", "spec"} for part in Path(path).parts)
    ]
    if not authentication_signals and authentication_files:
        authentication_signals = ["Authentication-related files detected; implementation not verified"]
    top_level_names = {Path(path).parts[0].casefold() for path in file_names if Path(path).parts}
    architectural_style = "Monorepo or multi-application layout" if len(top_level_names.intersection({"apps", "packages", "services"})) else "Layered/module structure inferred from paths" if api_files or database_files else "Not enough structural signals to infer architecture"

    return {
        "summary": summary,
        "languages": language_rows,
        "technologies": ordered_technologies,
        "top_level": [
            {"name": name, "type": kind}
            for name, kind in sorted(top_level.items(), key=lambda item: item[0].casefold())[:60]
        ],
        "manifest_files": sorted(manifests, key=str.casefold)[:40],
        "architecture": {"style": architectural_style, "project_map": _project_map(file_names)},
        "database": {"systems": database_systems, "evidence_files": database_files},
        "api": {"frameworks": api_frameworks, "route_files": api_files},
        "authentication": {"signals": authentication_signals, "evidence_files": authentication_files},
    }
