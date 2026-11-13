"""Client generation must not silently lose one operation of a multi-method route."""
import unittest
import warnings
from backend.api.main import app

class OpenApiContractTests(unittest.TestCase):
    def test_operation_ids_are_unique_and_media_methods_share_handler(self):
        previous = app.openapi_schema
        try:
            app.openapi_schema = None
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter('always')
                schema = app.openapi()
            self.assertFalse([w for w in caught if 'Duplicate Operation ID' in str(w.message)])
            ids = [op['operationId'] for path in schema['paths'].values()
                   for method, op in path.items() if method in {'get','head','post','put','patch','delete','options','trace'}]
            self.assertEqual(len(ids), len(set(ids)))
            media = schema['paths']['/storage/{media_path}']
            self.assertEqual(media['get']['operationId'], 'read_media_get')
            self.assertEqual(media['head']['operationId'], 'read_media_head')
            routes = [r for r in app.routes if getattr(r, 'path', '') == '/storage/{media_path:path}']
            self.assertEqual({m for r in routes for m in r.methods}, {'GET','HEAD'})
            self.assertEqual(len({r.endpoint for r in routes}), 1)
        finally:
            app.openapi_schema = previous
