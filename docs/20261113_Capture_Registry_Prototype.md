# W03 capture registry: linked opt-in candidate

This supersedes the earlier unconnected prototype. The approved policy is **fully stop transmission and acknowledge → change zone → restart with a fresh context**. Online rollover is not implemented.

See `20261113_Capture_Context_Integration.md` for implemented endpoints, migration, compatibility changes and validation limits. The low-level registry validation helper remains available, but the live API uses authenticated administrative session attribution rather than claiming independently verified physical location or bounded hardware clock error.
