-- revision: rev0000, down_revision: none
CREATE TABLE "items" (
    "id" SERIAL PRIMARY KEY,
    "title" VARCHAR(255) NOT NULL UNIQUE
);
