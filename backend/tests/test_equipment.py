import unittest

from backend.db.event_repository import get_connection
from backend.tests.support import isolated_api, login


class EquipmentApiTests(unittest.TestCase):
    def setUp(self):
        self.context = isolated_api()
        self.client = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.admin = login(self.client)
        self.client.put("/api/zones/프레스 구역",headers=self.admin,
                        json={"required_ppe":["helmet"],"rules":[]}).raise_for_status()

    def create(self, **changes):
        body={"name":"프레스 1호기","equipment_type":"press","zone_name":"프레스 구역","required_ppe":["vest"]}
        body.update(changes)
        response=self.client.post("/api/equipment",headers=self.admin,json=body)
        self.assertEqual(response.status_code,201,response.text)
        return response.json()

    def links(self, ids, camera="CAM_001"):
        return self.client.put(f"/api/cameras/{camera}/equipment",headers=self.admin,json={"equipment_ids":ids})

    def policy(self, camera="CAM_001"):
        response=self.client.get(f"/api/cameras/{camera}/ppe-policy",headers=self.admin)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_crud_filters_patch_and_idempotent_deactivation(self):
        equipment=self.create(name="  프레스 1호기  ")
        item_path=f"/api/equipment/{equipment['equipment_id']}"
        self.assertEqual(equipment["name"],"프레스 1호기")
        self.assertEqual(self.client.get(item_path,headers=self.admin).json(),equipment)
        update=self.client.patch(item_path,headers=self.admin,json={"name":"프레스 2호기","equipment_type":None,"required_ppe":[]})
        self.assertEqual(update.status_code,200,update.text)
        self.assertIsNone(update.json()["equipment_type"])
        self.assertEqual(update.json()["required_ppe"],[])
        self.assertEqual(update.json()["created_at"],equipment["created_at"])
        deleted=self.client.delete(item_path,headers=self.admin)
        self.assertEqual(deleted.json()["status"],"inactive")
        self.assertEqual(self.client.delete(item_path,headers=self.admin).json(),deleted.json())
        self.assertEqual(self.client.get("/api/equipment",headers=self.admin,params={"status":"active"}).json(),{"items":[]})
        self.assertEqual(len(self.client.get("/api/equipment",headers=self.admin).json()["items"]),1)
        self.assertEqual(self.client.get("/api/equipment",headers=self.admin,params={"zone_name":"없는 구역"}).json(),{"items":[]})

    def test_validation_and_unknown_objects(self):
        valid={"name":"장비","zone_name":"프레스 구역","required_ppe":[]}
        for patch in ({"required_ppe":["unknown"]},{"required_ppe":["helmet","helmet"]},
                      {"name":"   "},{"zone_name":"없는 구역"},{"unexpected":True}):
            self.assertEqual(self.client.post("/api/equipment",headers=self.admin,json={**valid,**patch}).status_code,422)
        equipment=self.create()
        for body in ({"name":None},{"zone_name":None},{"required_ppe":None},{"status":None}):
            response=self.client.patch(f"/api/equipment/{equipment['equipment_id']}",headers=self.admin,json=body)
            self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(self.client.get("/api/equipment/missing",headers=self.admin).status_code,404)
        self.assertEqual(self.links(["missing"]).status_code,404)
        self.assertEqual(self.links([],camera="missing").status_code,404)

    def test_every_endpoint_requires_admin(self):
        equipment=self.create()
        worker=login(self.client,"worker01")
        routes=[("GET","/api/equipment",None),
                ("POST","/api/equipment",{"name":"장비","zone_name":"프레스 구역","required_ppe":[]}),
                ("GET",f"/api/equipment/{equipment['equipment_id']}",None),
                ("PATCH",f"/api/equipment/{equipment['equipment_id']}",{"name":"변경"}),
                ("DELETE",f"/api/equipment/{equipment['equipment_id']}",None),
                ("GET","/api/cameras/CAM_001/equipment",None),
                ("PUT","/api/cameras/CAM_001/equipment",{"equipment_ids":[]}),
                ("GET","/api/cameras/CAM_001/ppe-policy",None)]
        for method,path,body in routes:
            self.assertEqual(self.client.request(method,path,json=body).status_code,401)
            self.assertEqual(self.client.request(method,path,headers=worker,json=body).status_code,403)

    def test_mapping_replace_is_atomic_and_preserves_input_order(self):
        first=self.create()
        second=self.create(name="두 번째 장비",required_ppe=["gloves"])
        ids=[second["equipment_id"],first["equipment_id"]]
        response=self.links(ids)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()["equipment_ids"],ids)
        self.assertEqual(self.links([first["equipment_id"],"missing"]).status_code,404)
        self.assertEqual(self.client.get("/api/cameras/CAM_001/equipment",headers=self.admin).json()["equipment_ids"],ids)
        self.assertEqual(self.links([first["equipment_id"],first["equipment_id"]]).status_code,422)

    def test_zone_inactive_and_connected_move_guards(self):
        equipment=self.create()
        equipment_id=equipment["equipment_id"]
        item_path=f"/api/equipment/{equipment_id}"
        self.assertEqual(self.links([equipment_id],camera="CAM_002").json()["detail"],"equipment_zone_mismatch")
        self.links([equipment_id]).raise_for_status()
        response=self.client.patch(item_path,headers=self.admin,json={"zone_name":"자재 이동 구역"})
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.json()["detail"],"equipment_has_camera_links")
        self.client.delete(item_path,headers=self.admin).raise_for_status()
        self.assertEqual(self.links([equipment_id]).json()["detail"],"equipment_inactive")
        self.links([]).raise_for_status()
        self.assertEqual(self.client.patch(item_path,headers=self.admin,json={"zone_name":"자재 이동 구역"}).status_code,200)
        self.client.put("/api/zones/장비 전용 구역",headers=self.admin,json={"required_ppe":[],"rules":[]}).raise_for_status()
        self.create(zone_name="장비 전용 구역",status="inactive")
        response=self.client.delete("/api/zones/장비 전용 구역",headers=self.admin)
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(response.json()["detail"],"zone_has_equipment")

    def test_policy_uses_only_mapped_active_equipment_and_tracks_support(self):
        equipment=self.create(required_ppe=["helmet","vest","gloves"])
        self.create(name="연결하지 않은 장비",required_ppe=["goggles"])
        self.links([equipment["equipment_id"]]).raise_for_status()
        policy=self.policy()
        self.assertTrue(policy["configured"])
        self.assertEqual(policy["zone_required_ppe"],["helmet"])
        self.assertEqual(policy["required_ppe"],["helmet","vest","gloves"])
        self.assertEqual(policy["unsupported_ppe"],["gloves"])
        self.assertEqual(policy["detectable_ppe"],["helmet","vest"])
        path=f"/api/equipment/{equipment['equipment_id']}"
        self.client.delete(path,headers=self.admin).raise_for_status()
        self.assertEqual(self.policy()["required_ppe"],["helmet"])
        self.assertEqual(self.client.get("/api/cameras/CAM_001/equipment",headers=self.admin).json()["equipment_ids"],[equipment["equipment_id"]])
        self.client.patch(path,headers=self.admin,json={"status":"active"}).raise_for_status()
        self.assertEqual(self.policy()["required_ppe"],["helmet","vest","gloves"])

    def test_unconfigured_and_explicit_empty_rules_are_distinct(self):
        policy=self.policy()
        self.assertFalse(policy["configured"])
        self.assertIsNone(policy["required_ppe"])
        self.assertIn("equipment_scope_unconfigured",policy["configuration_issues"])
        self.client.put("/api/zones/프레스 구역",headers=self.admin,json={"required_ppe":[],"rules":[]}).raise_for_status()
        self.links([]).raise_for_status()
        self.assertTrue(self.policy()["configured"])
        self.assertEqual(self.policy()["required_ppe"],[])
        with get_connection() as conn:
            conn.execute("INSERT INTO camera_info(camera_id) VALUES('CAM_UNASSIGNED')")
            conn.execute("INSERT INTO camera_info(camera_id,zone_name) VALUES('CAM_NO_RULE','규칙 없음')")
        self.assertIn("camera_zone_unassigned",self.policy("CAM_UNASSIGNED")["configuration_issues"])
        self.assertIn("zone_rule_missing",self.policy("CAM_NO_RULE")["configuration_issues"])

    def test_v4_migration_preserves_existing_records_and_is_repeatable(self):
        from backend.db.migrations import apply_migrations, LATEST_VERSION
        with get_connection() as conn:
            before=[dict(row) for row in conn.execute("SELECT * FROM candidate_event ORDER BY event_id")]
            for table in ("camera_equipment","camera_equipment_config","equipment"):
                conn.execute(f"DROP TABLE {table}")
            conn.execute("PRAGMA user_version=4")
            conn.commit()
            apply_migrations(conn)
            apply_migrations(conn)
            self.assertEqual([dict(row) for row in conn.execute("SELECT * FROM candidate_event ORDER BY event_id")],before)
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0],LATEST_VERSION)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM equipment").fetchone()[0],0)
        self.assertEqual(self.client.get("/api/equipment",headers=self.admin).status_code,200)
