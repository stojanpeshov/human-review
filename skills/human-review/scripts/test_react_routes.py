#!/usr/bin/env python3
"""react_routes: the screens of a React Router app, read off its route modules.

The fixture is mitt-ludvig's shape in miniature: a `routes.tsx` whose paths are calls into
a helper object with defaulted `:param`s, a layout that only provides and outlets, a
sub-route array imported from a module of its own (with its own helper), a catch-all, and
a helper whose body is a block. Nothing is executed; everything is parsed.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("react_routes", HERE / "react_routes.py")
rr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rr)

ROUTES = '''import AppConfig from "@/app-config";
import { Outlet } from "react-router-dom";
import { HomePage } from "@/views/home";
import CustomerPageLayout from "@/views/customer";
import CustomerMainPage from "@/views/customer/main";
import { ManageProvider } from "@/views/manage/context";
import manageRoutes from "@/views/manage/routes/manageRoutes";
import Settings from "@/views/settings";

export const AppRouteHelper = {
  getHome: () => "/home",
  // a comment with "quotes" and `backticks`
  getCustomerPage: (customerId: UrlParamType = ":customerId") =>
    `/assignments/customers/${customerId}/customer-page`,
  getBatches: (batchId?: string) =>
    batchId ? `/batches/${batchId}` : "/batches",
  getAssignments: (focusedClient?: string) => {
    let queryParams = "";
    if (focusedClient) {
      queryParams += `focusedClient=${focusedClient}&`;
    }
    return queryParams ? `/assignments?${queryParams}` : "/assignments";
  },
  getSettings: (customerId: UrlParamType = ":customerId", fromPage = false) => {
    let path = `/customers/${customerId}/settings`;
    if (fromPage) {
      path += "?fromCustomerPage=true";
    }
    return path;
  },
  getManage: () => "/manage",
  getPowerBi: () => AppConfig.POWER_BI_URL,
};

const routes: RouteConfig[] = [
  { path: AppRouteHelper.getHome(), element: <HomePage />, subRoutes: [] },
  { path: AppRouteHelper.getBatches(), element: <Batches />, subRoutes: [] },
  { path: AppRouteHelper.getBatches(":batchId"), element: <Batches />, subRoutes: [] },
  { path: AppRouteHelper.getAssignments(), element: <Assignments />, subRoutes: [] },
  {
    path: AppRouteHelper.getCustomerPage(),
    element: (
      <CustomerPageLayout>
        <CustomerMainPage />
      </CustomerPageLayout>
    ),
    subRoutes: [],
  },
  { path: AppRouteHelper.getSettings(), element: <Settings />, subRoutes: [] },
  {
    path: AppRouteHelper.getManage(),
    element: (
      <ManageProvider>
        <Outlet />
      </ManageProvider>
    ),
    subRoutes: manageRoutes,
  },
  { path: AppRouteHelper.getPowerBi(), element: <PowerBi />, subRoutes: [] },
  { path: "/fluentui", element: (<><div>Examples:</div><FirstExample /></>), subRoutes: [] },
  { path: "*", element: <p>No such route!</p>, subRoutes: [] },
];

export const extraRoutes: RouteConfig[] = [
  { path: "/deals", element: <DealsPage />, subRoutes: [] },
];

export default routes;
'''

MANAGE = '''import { RouteConfig } from "@/routes";
import ManageLanding from "..";
import ListOfCustomers from "../components/ListOfCustomers";

export const ManageRouteHelper = {
  getLanding: () => "/manage",
  getList: () => "/manage/list-of-customers",
};

const manageRoutes: RouteConfig[] = [
  { path: ManageRouteHelper.getLanding(), element: <ManageLanding />, subRoutes: [] },
  { path: ManageRouteHelper.getList(), element: <ListOfCustomers />, subRoutes: [] },
  {
    path: "nested",
    element: <Outlet />,
    subRoutes: [
      { path: "", element: <NestedIndex />, subRoutes: [] },
      { path: ":edit", element: <NestedEdit />, subRoutes: [] },
    ],
  },
  { path: "*", element: <p>No such route!</p>, subRoutes: [] },
];

export default manageRoutes;
'''


def _app(tmp_path: Path) -> Path:
    src = tmp_path / "frontend" / "src"
    (src / "views" / "manage" / "routes").mkdir(parents=True)
    (src / "views" / "manage" / "components").mkdir()
    (src / "views" / "customer" / "main").mkdir(parents=True)
    (src / "routes.tsx").write_text(ROUTES)
    (src / "views" / "manage" / "routes" / "manageRoutes.tsx").write_text(MANAGE)
    (src / "views" / "manage" / "index.tsx").write_text("export default () => null;\n")
    (src / "views" / "manage" / "components" / "ListOfCustomers.tsx").write_text("x\n")
    (src / "views" / "customer" / "main" / "index.tsx").write_text("x\n")
    return src / "routes.tsx"


def test_paths_are_read_through_the_helper_with_their_default_params(tmp_path):
    routes = rr.discover(_app(tmp_path))
    paths = [r["path"] for r in routes if not r.get("unresolved")]
    assert paths == ["/home", "/batches", "/batches/:batchId", "/assignments",
                     "/assignments/customers/:customerId/customer-page",
                     "/customers/:customerId/settings",
                     "/manage", "/manage/list-of-customers", "/manage/nested",
                     "/manage/nested/:edit", "/fluentui", "/deals"]


def test_a_screen_is_named_by_the_component_it_renders_not_its_wrappers(tmp_path):
    by = {r["path"]: r for r in rr.discover(_app(tmp_path)) if r.get("path")}
    page = by["/assignments/customers/:customerId/customer-page"]
    assert page["component"] == "CustomerMainPage"
    assert page["file"] == "frontend/src/views/customer/main/index.tsx"
    assert by["/fluentui"]["component"] == "FirstExample"
    # The layout that only provides and outlets is not a screen: its landing child is.
    assert by["/manage"]["component"] == "ManageLanding"
    assert by["/manage/list-of-customers"]["file"] == \
        "frontend/src/views/manage/components/ListOfCustomers.tsx"


def test_what_the_subset_cannot_read_is_said_never_guessed(tmp_path):
    odd, = [r for r in rr.discover(_app(tmp_path)) if r.get("unresolved")]
    assert odd["expr"] == "AppRouteHelper.getPowerBi()" and odd["component"] == "PowerBi"


def test_static_routes_are_screens_and_param_routes_wait_for_a_value(tmp_path):
    routes = rr.discover(_app(tmp_path))
    screens, unvisited = rr.plan_screens(routes, {
        "Customer page": "assignments/customers/1001/customer-page",
        "fluentui": None,
    })
    assert screens["home"] == "home" and screens["manage/list-of-customers"] == \
        "manage/list-of-customers"
    assert screens["Customer page"] == "assignments/customers/1001/customer-page"
    assert "fluentui" not in screens
    assert not any(":" in v for v in screens.values())
    missing = {u["route"]: u for u in unvisited}
    assert set(missing) == {"/batches/:batchId", "/customers/:customerId/settings",
                            "/manage/nested/:edit", "AppRouteHelper.getPowerBi()"}
    assert missing["/customers/:customerId/settings"]["params"] == ["customerId"]
    assert missing["AppRouteHelper.getPowerBi()"]["reason"] == "unresolved"


def test_an_override_that_matches_no_route_is_a_screen_of_its_own(tmp_path):
    screens, _ = rr.plan_screens([{"path": "/home", "component": "HomePage"}],
                                 {"Deep link": "/home?tab=2", "Other": "elsewhere"})
    assert screens == {"Deep link": "home?tab=2", "Other": "elsewhere"}


def test_the_entry_module_is_found_where_a_vite_app_keeps_it(tmp_path):
    entry = _app(tmp_path)
    assert rr.find_entry(tmp_path) == entry
    assert rr.find_entry(tmp_path, "frontend/src/routes.tsx") == entry
    assert rr.find_entry(tmp_path, "nope.tsx") is None
    assert rr.find_entry(tmp_path / "frontend" / "src" / "views") is None
