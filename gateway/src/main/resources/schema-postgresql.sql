-- PostgreSQL schema, written by hand for one reason: everything here is
-- CREATE ... IF NOT EXISTS.
--
-- Spring ships both of these tables' definitions, and both ship as plain
-- CREATE TABLE, which fails the second time the application starts. That was
-- survivable when the database was thrown away on every run. It is not now.

-- Where tokens are kept between restarts. The refresh token is the row that
-- matters: without it a returning user has an access token good for an hour
-- and no way to renew it, and the failure turns up an hour after sign-in.
CREATE TABLE IF NOT EXISTS oauth2_authorized_client (
  client_registration_id  varchar(100)  NOT NULL,
  principal_name          varchar(200)  NOT NULL,
  access_token_type       varchar(100)  NOT NULL,
  access_token_value      bytea      NOT NULL,
  access_token_issued_at  timestamp     NOT NULL,
  access_token_expires_at timestamp     NOT NULL,
  access_token_scopes     varchar(1000) DEFAULT NULL,
  refresh_token_value     bytea      DEFAULT NULL,
  refresh_token_issued_at timestamp     DEFAULT NULL,
  created_at              timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (client_registration_id, principal_name)
);
