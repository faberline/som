// The shape of src/schema.ts. This file is the same in every generated product.

export type FieldType = "str" | "int" | "float" | "bool" | "date";

export type Value = string | number | boolean | null;

export type FieldSpec = {
  // The JSON member, query parameter and form label of the field.
  name: string;
  type: FieldType;
  // A required field is sent on every create and is never null.
  required: boolean;
  // A nullable field accepts null on create and patch.
  nullable: boolean;
  unique: boolean;
  // The server default of an optional field; undefined for a required field.
  default?: Value;
};

export type FilterSpec = {
  // The field the filter compares, and the query parameter that carries it.
  field: string;
  param: string;
  type: FieldType;
};

export type QuerySpec = {
  filter: FilterSpec | null;
  // The date field compared by the exclusive before filter, sent as `before`.
  before: string | null;
  // Pages hold pageSize rows, sent as limit, from offset.
  pageSize: number;
  maxLimit: number;
};

export type ResourceSpec = {
  name: string;
  path: string;
  // Rows carry created_at and updated_at.
  timestamps: boolean;
};
