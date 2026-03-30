"""Generates JPA @Entity classes and JpaRepository interfaces from EntityMetadata."""

from __future__ import annotations

from tsql_migration.state import EntityMetadata, ColumnMetadata


class EntityGenerator:
    """Generate Spring Boot 3 / Java 21 JPA entity and repository source code."""

    def __init__(self, base_package: str) -> None:
        self.base_package = base_package

    def generate_entity(self, entity: EntityMetadata) -> str:
        """Generate a JPA @Entity class for the given table."""
        class_name = entity.entity_class_name
        imports = self._collect_imports(entity)
        fields = self._generate_fields(entity)
        getters_setters = self._generate_getters_setters(entity)

        return f"""\
package {self.base_package}.entity;

{imports}

@Entity
@Table(name = "{entity.table_name}", schema = "{entity.schema_name}")
public class {class_name} {{

{fields}

{getters_setters}
}}
"""

    def generate_repository(self, entity: EntityMetadata) -> str:
        """Generate a JpaRepository interface."""
        class_name = entity.entity_class_name
        pk_type = self._get_pk_java_type(entity)

        return f"""\
package {self.base_package}.repository;

import {self.base_package}.entity.{class_name};
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

@Repository
public interface {class_name}Repository extends JpaRepository<{class_name}, {pk_type}> {{
}}
"""

    def _collect_imports(self, entity: EntityMetadata) -> str:
        """Collect required Java imports based on column types."""
        imports: set[str] = {
            "jakarta.persistence.*",
        }

        for col in entity.columns:
            java_type = col.java_type
            if java_type == "BigDecimal":
                imports.add("java.math.BigDecimal")
            elif java_type in ("LocalDate", "LocalDateTime", "LocalTime"):
                imports.add(f"java.time.{java_type}")
            elif java_type == "OffsetDateTime":
                imports.add("java.time.OffsetDateTime")
            elif java_type == "UUID":
                imports.add("java.util.UUID")

        return "\n".join(f"import {imp};" for imp in sorted(imports))

    def _generate_fields(self, entity: EntityMetadata) -> str:
        """Generate field declarations with JPA annotations."""
        lines: list[str] = []
        for col in entity.columns:
            annotations = self._field_annotations(col, entity)
            field_name = to_camel_case(col.name)
            java_type = col.java_type

            for ann in annotations:
                lines.append(f"    {ann}")
            lines.append(f"    private {java_type} {field_name};")
            lines.append("")

        return "\n".join(lines)

    def _field_annotations(self, col: ColumnMetadata, entity: EntityMetadata) -> list[str]:
        """Generate JPA annotations for a field."""
        annotations: list[str] = []

        if col.is_primary_key:
            annotations.append("@Id")
            if col.is_auto_increment:
                annotations.append("@GeneratedValue(strategy = GenerationType.IDENTITY)")

        col_ann_parts = [f'name = "{col.name}"']
        if not col.nullable:
            col_ann_parts.append("nullable = false")
        if col.max_length and col.java_type == "String":
            col_ann_parts.append(f"length = {col.max_length}")

        annotations.append(f"@Column({', '.join(col_ann_parts)})")

        return annotations

    def _generate_getters_setters(self, entity: EntityMetadata) -> str:
        """Generate getter and setter methods."""
        lines: list[str] = []
        for col in entity.columns:
            field_name = to_camel_case(col.name)
            method_name = field_name[0].upper() + field_name[1:]
            java_type = col.java_type

            # Getter
            lines.append(f"    public {java_type} get{method_name}() {{")
            lines.append(f"        return {field_name};")
            lines.append("    }")
            lines.append("")

            # Setter
            lines.append(f"    public void set{method_name}({java_type} {field_name}) {{")
            lines.append(f"        this.{field_name} = {field_name};")
            lines.append("    }")
            lines.append("")

        return "\n".join(lines)

    def _get_pk_java_type(self, entity: EntityMetadata) -> str:
        """Determine the primary key Java type for repository generic."""
        for col in entity.columns:
            if col.is_primary_key:
                return col.java_type
        return "Long"  # default


def to_camel_case(name: str) -> str:
    """Convert column name to camelCase: order_id -> orderId."""
    parts = name.replace("-", "_").split("_")
    return parts[0].lower() + "".join(p.capitalize() for p in parts[1:])
