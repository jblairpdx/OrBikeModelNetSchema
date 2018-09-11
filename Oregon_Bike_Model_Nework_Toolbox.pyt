"""Tools relating to bicycle network/model."""  # pylint: disable=too-many-lines
from collections import Counter, defaultdict
import copy
import datetime
import math
import os
import random
import string
from types import FunctionType
import uuid

import arcpy

##TODO: Create repo for tool & template GDB.
"""Improvement ideas:

* Bike_Facilities valid values routine.
    1. Create valid_tables.
    2. Map attribute to valid list.
    3. Create function & execute-call to validate (just warning for now).

* Parse & rewrite dates not in ISO in active_isodate. Warning if unparseable.

* For `oneway`, convert "BW" to empty string "".

* Warnings:
    * If is_sidewalk=1 and sidewalk_width <= 0
    * If is_sidewalk=0 and sidewalk_width > 0.
    * Missing values that shouldn't be missing.
    * If bridge=0 and brg_nofac=1 or brg_nosep=1.
    * If brg_nofac=1 and brg_nosep=0.

* Rather than raise an exception when no conflation fields chosen, update messages on
    field parameters to indicate at least one must be chosen.

* New tool: Find links that may need splitting, links that could be merged or combined.
"""

META = {"label": os.path.splitext(os.path.basename(__file__))[0].replace("_", " ")}
"""dict: Toolbox metadata."""

CLEANER = {
    # Integer.
    "Zero missing integer": (lambda x: 0 if not x else x),
    # String.
    "Clean whitespace": (lambda x: clean_whitespace(x, clear_empty_string=False)),
    """Convert empty string to "Neither\"""": (lambda x: "Neither" if not x else x),
    """Convert empty string to "tbv" (to be verified)""": (
        lambda x: "tbv" if not x else x
    ),
    "Empty missing string": (lambda x: "" if not x else x),
    "Remove invalid string": (lambda x: "" if x in INVALID_STRINGS else x),
}
"""dict: Mapping of cleaner description to function."""
# Order is important here.
CLEANER["Common string cleaning"] = [
    CLEANER["Clean whitespace"],
    CLEANER["Remove invalid string"],
    CLEANER["Empty missing string"],
]

ATTRIBUTE_CLEANER_KEYS = {
    # Core attributes.
    "link_id": [],  # Already updated in execute.
    "str_dir": ["Common string cleaning"],
    "str_name": ["Common string cleaning"],
    "str_type": ["Common string cleaning"],
    "str_name2": ["Common string cleaning"],
    # Classification attributes.
    "fc_code": ["Zero missing integer"],
    "fc_desc": ["Common string cleaning"],
    "bikefac": [
        "Common string cleaning",
        """Convert empty string to "tbv" (to be verified)""",
    ],
    "bridge": ["Zero missing integer"],
    "brg_nofac": ["Zero missing integer"],
    "brg_nosep": ["Zero missing integer"],
    # Bicycle facility attributes.
    "ow_rest": ["Common string cleaning"],
    "lturn_rest": ["Common string cleaning"],
    "rturn_rest": ["Common string cleaning"],
    "thru_rest": ["Common string cleaning"],
    "cnt_vol": [],  # Nulls managed in outputs. ##TODO: -9999?
    "est_vol": [],  # Nulls managed in outputs. ##TODO: -9999?
    "bike_rest": ["Zero missing integer"],
    "bike_oneway_restriction": ["Common string cleaning"],
    "bike_lane_width": [],  # Nulls managed in outputs. ##TODO: -9999?
    "num_lanes": [],  # Nulls managed in outputs. ##TODO: -9999?
    "left_turn": ["Zero missing integer"],
    "right_turn": ["Zero missing integer"],
    "median": ["Zero missing integer"],
    "parking": ["Zero missing integer"],
    "speed": [],  # Nulls managed in outputs. ##TODO: -9999?
    "stop": ["Common string cleaning", """Convert empty string to "Neither\""""],
    "signal": ["Common string cleaning", """Convert empty string to "Neither\""""],
    "bike_sign": ["Zero missing integer"],
    "slm": ["Common string cleaning"],
    "is_sidewalk": [],  # Nulls managed in outputs. ##TODO: -9999?
    "sidewalk_width": [],  # Nulls managed in outputs. ##TODO: -9999?
    # Geometry attributes.
    "fnode": [],  # Updated later in execute.
    "tnode": [],  # Updated later in execute.
    "fbearing": [],  # Updated later in execute.
    "tbearing": [],  # Updated later in execute.
    "dst_ft": [],  # Updated later in execute.
    "dzp": ["Zero missing integer"],
    "dzn": ["Zero missing integer"],
    "z_F": ["Zero missing integer"],
    "z_T": ["Zero missing integer"],
    "shape": [],  # Cleaned separately in execute.
    # Maintenance attributes.
    "origin_reference": ["Common string cleaning"],
    "origin_id_repr": ["Common string cleaning"],
    "active_isodate": ["Common string cleaning"],
    "ntedit_flag": ["Common string cleaning"],
    # Geographic overlay attributes.
    "taz_num": [],  # Nulls managed in outputs. ##TODO: -9999?
    "ugb_code": ["Common string cleaning"],
    "inside_mpo": [],  # Nulls managed in outputs. ##TODO: -9999?
    # Geographic conflation attributes.
    "fclass": ["Common string cleaning"],
    "fed_class": ["Common string cleaning"],
}
"""dict of lists: Mapping of attribute name to functions to clean them with."""
INVALID_STRINGS = ["null", "Null", "NULL", "<null>", "<Null>", "<NULL>"]
"""list: Collection of string attributes considered invalid."""


class LicenseError(Exception):
    """Generic error for license check-out failure."""


class Toolbox(object):  # pylint: disable=too-few-public-methods
    """Defines the toolbox.

    Toolbox class is required for constructing an ArcGIS Python toolbox.
    The name of toolbox is the basename of this file.

    Use arcpy.ImportToolbox to attach the toolbox. After attaching,
    reference the tools like `arcpy.{toolbox-alias}.{tool-class-name}`
    """

    def __init__(self):
        """Initialize instance."""
        self.label = META["label"]
        """str: Label for the toolbox. Only shows up in toolbox properties."""
        self.alias = "bikemod"
        """str: toolbox namespace when attached to ArcPy."""
        self.tools = [
            Update_Bike_Model_01_Base_Attributes,
            Update_Bike_Model_02_Base_Elevation_Attributes,
            Update_Bike_Model_03_Overlay_Attributes,
            Update_Bike_Model_04_Conflation_Attributes,
        ]
        """list: Tool classes associated with this toolbox."""


class Update_Bike_Model_01_Base_Attributes(object):  # pylint: disable=invalid-name
    """Updates & clean base attributes on bike model network, where automate-able.

    Bike_Facilities attributes updated by tool: link_id, dst_ft, fnode, tnode,
        fbearing, tbearing.
    Datasets updated by tool: Link_ID_Transforms, Nodes, TAZ_Centroids, TAZ_Links,
        TAZ_Nodes.
    """

    def __init__(self):
        """Initialize instance."""
        self.label = "Update Bike Model 01: Base Attributes"
        """str: How tool is named within toolbox."""
        self.category = None
        """str, NoneType: Name of sub-toolset tool will be in (optional)."""
        self.description = self.__class__.__doc__
        self.canRunInBackground = False
        """bool: Flag for whether tool controls ArcGIS focus while running."""

    def getParameterInfo(self):  # pylint: disable=no-self-use
        """Load parameters into toolbox.

        Recommended: Use `create_parameter` to allow initial
        definition to be a dictionary attribute map.

        Returns:
            list of arcpy.Parameter: Tool parameters.
        """
        parameters = [
            create_parameter(
                name="container_path",
                displayName="Bike Route Network Container",
                datatype="DEWorkspace",
                parameterType="Required",
            ),
            create_parameter(
                name="bike_facilities_path",
                displayName="Bike Facilities Dataset",
                datatype="GPFeatureLayer",
                enabled=False,
                parameterType="Required",
            ),
            create_parameter(
                name="link_id_transforms_path",
                displayName="Link ID Transforms Dataset",
                datatype="GPTableView",
                enabled=False,
                parameterType="Required",
            ),
            create_parameter(
                name="nodes_path",
                displayName="Nodes Dataset",
                datatype="GPFeatureLayer",
                enabled=False,
                parameterType="Required",
            ),
            create_parameter(
                name="taz_centroids_path",
                displayName="TAZ-Centroids Dataset",
                datatype="GPFeatureLayer",
                enabled=False,
                parameterType="Required",
            ),
            create_parameter(
                name="taz_links_path",
                displayName="TAZ-Links Dataset",
                datatype="GPFeatureLayer",
                enabled=False,
                parameterType="Required",
            ),
            create_parameter(
                name="taz_nodes_path",
                displayName="TAZ-Nodes Dataset",
                datatype="GPFeatureLayer",
                enabled=False,
                parameterType="Required",
            ),
        ]
        return parameters

    def updateParameters(self, parameters):  # pylint: disable=no-self-use
        """Modify parameters before internal validation is performed.

        This method is called whenever a parameter has been changed.

        Args:
            parameters (list of arcpy.Parameter): Tool parameters.
        """
        parameter = {param.name: param for param in parameters}
        if parameter_changed(parameter["container_path"]) and arcpy.Exists(
            parameter_value(parameter["container_path"])
        ):
            # Attempt to find & assign dataset parameters.
            arcpy.env.workspace = parameter_value(parameter["container_path"])
            for parameter_name, dataset_name in [
                ("bike_facilities_path", "Bike_Facilities"),
                ("link_id_transforms_path", "Link_ID_Transforms"),
                ("nodes_path", "Nodes"),
                ("taz_centroids_path", "TAZ_Centroids"),
                ("taz_links_path", "TAZ_Links"),
                ("taz_nodes_path", "TAZ_Nodes"),
            ]:
                parameter[parameter_name].value = os.path.join(
                    arcpy.env.workspace, dataset_name
                )

    def execute(self, parameters, messages):  # pylint: disable=no-self-use
        """Execute tool procedure.

        Args:
            parameters (list of arcpy.Parameter): Tool parameters.
            messages (geoprocessing messages object): Tool messages.
        """
        value = parameter_value_map(parameters)
        # Uncomment loop to have info about parameter values in messages.
        # for param in parameters:
        #     messages.AddWarningMessage(param.name + " - " + param.datatype)
        #     messages.AddWarningMessage(value[param.name])
        arcpy.env.workspace = value["container_path"]
        for name in ["Bike_Facilities", "TAZ_Centroids"]:
            if feature_count(name) == 0:
                messages.addErrorMessage(
                    "`{}` has no link features; ending run.".format(name)
                )
                return

        messages.addMessage(
            "Start: Update `link_id` (must be done before other updates)."
        )
        updates = update_link_ids()
        messages.addMessage("....{new} new IDs; {changed} changed.".format(**updates))
        messages.addMessage("End: Update.")
        """Cleaning & replacing values should be done before deriving other attributes.

        Most of this consists of taking null data and other "empty" types & converting
        them to a more amenable "empty" type for model usage (generally "" for strings
        and 0 for integers).
        """
        messages.addMessage("Start: Clean `Bike_Facility` attributes.")
        for attr_key, cleaner_keys in ATTRIBUTE_CLEANER_KEYS.items():
            messages.addMessage("Attribute: `{}`.".format(attr_key))
            if not cleaner_keys:
                messages.addMessage("....No cleaning functions.")
            for key in cleaner_keys:
                updates = clean_attributes(attr_key, CLEANER[key])
                messages.addMessage("....`{}` - {} changes made.".format(key, updates))
        messages.addMessage("End: Clean.")
        messages.addMessage(
            "Start: Repair invalid geometry; remove features without geometry."
        )
        arcpy.management.RepairGeometry("Bike_Facilities", delete_null=True)
        messages.addMessage("End: Repair.")
        messages.addMessage("Start: Update descriptions (`fc_desc`).")
        updates = update_descriptions()
        messages.addMessage("....{} changes made.".format(updates))
        messages.addMessage("End: Update.")
        # Must occur before update_nodes.
        messages.addMessage("Start: Update Node IDs.")
        updates = update_node_ids()
        messages.addMessage("....{} changes made.".format(updates))
        messages.addMessage("Start: Update `Nodes` dataset.")
        updates = update_nodes()
        messages.addMessage("....{new} new nodes; {changed} changed.".format(**updates))
        messages.addMessage("Start: Update link end-bearings (`fbearing` & `tbearing`.")
        updates = update_bearings()
        messages.addMessage("....{} changes made.".format(updates))
        messages.addMessage("End: Update.")
        messages.addMessage("Start: Update link distance (`dst_ft`).")
        updates = update_distances()
        messages.addMessage("....{} changes made.".format(updates))
        messages.addMessage("End: Update.")
        messages.addMessage("Start: Update `TAZ_Nodes` dataset.")
        # No idea why this distance chosen, or why it is hard-coded.
        updates = update_taz_nodes(search_distance="128 Feet")
        messages.addMessage("....{} changes made.".format(updates))
        messages.addMessage("End: Update.")
        messages.addMessage("Start: Update `TAZ_Links` dataset.")
        updates = update_taz_links()
        messages.addMessage("....{new} new links; {changed} changed.".format(**updates))
        messages.addMessage("End: Update.")


class Update_Bike_Model_02_Base_Elevation_Attributes(object):  # pylint: disable=invalid-name
    """Updates base elevation attributes on bike model/network, where automate-able.

    Bike_Facilities attributes updated by tool: dzp, dzn.

    This tool requires the 3D Analyst ArcGIS extension.
    """

    def __init__(self):
        """Initialize instance."""
        self.label = "Update Bike Model 02: Base Elevation Attributes"
        """str: How tool is named within toolbox."""
        self.category = None
        """str, NoneType: Name of sub-toolset tool will be in (optional)."""
        self.description = self.__class__.__doc__
        """str: Longer text describing tool, shown in side panel."""
        self.canRunInBackground = False
        """bool: Flag for whether tool controls ArcGIS focus while running."""

    def getParameterInfo(self):  # pylint: disable=no-self-use
        """Load parameters into toolbox.

        Recommended: Use `create_parameter` to allow initial
        definition to be a dictionary attribute map.

        Returns:
            list of arcpy.Parameter: Tool parameters.
        """
        parameters = [
            create_parameter(
                name="container_path",
                displayName="Bike Route Network Container",
                datatype="DEWorkspace",
                parameterType="Required",
            ),
            create_parameter(
                name="bike_facilities_path",
                displayName="Bike Facility Dataset",
                datatype="GPFeatureLayer",
                enabled=False,
                parameterType="Required",
            ),
            create_parameter(
                name="surface_path",
                displayName="Elevation Surface Dataset",
                datatype="GPRasterLayer",
                parameterType="Required",
            ),
        ]
        return parameters

    def isLicensed(self):  # pylint: disable=no-self-use
        """Set whether tool is licensed to execute.

        If tool needs extra licensing, returning False prevents execution.

        Returns:
            bool: True if licensed, False otherwise.
        """
        return True if arcpy.CheckExtension("3D") == "Available" else False

    def updateParameters(self, parameters):  # pylint: disable=no-self-use
        """Modify parameters before internal validation is performed.

        This method is called whenever a parameter has been changed.

        Args:
            parameters (list of arcpy.Parameter): Tool parameters.
        """
        parameter = {param.name: param for param in parameters}
        if parameter_changed(parameter["container_path"]) and arcpy.Exists(
            parameter_value(parameter["container_path"])
        ):
            # Attempt to find & assign dataset parameters.
            arcpy.env.workspace = parameter_value(parameter["container_path"])
            for parameter_name, dataset_name in [
                ("bike_facilities_path", "Bike_Facilities")
            ]:
                parameter[parameter_name].value = os.path.join(
                    arcpy.env.workspace, dataset_name
                )

    def execute(self, parameters, messages):  # pylint: disable=no-self-use
        """Execute tool procedure.

        Args:
            parameters (list of arcpy.Parameter): Tool parameters.
            messages (geoprocessing messages object): Tool messages.
        """
        value = parameter_value_map(parameters)
        # Uncomment loop to have info about parameter values in messages.
        # for param in parameters:
        #     messages.AddWarningMessage(param.name + " - " + param.datatype)
        #     messages.AddWarningMessage(value[param.name])
        arcpy.env.workspace = value["container_path"]
        for name in ["Bike_Facilities"]:
            if feature_count(name) == 0:
                messages.addErrorMessage(
                    "`{}` has no link features; ending run.".format(name)
                )
                return

        messages.addMessage("Start: Update elevation deltas `dzp` & `dzn`.")
        updates = update_elevation_deltas(value["surface_path"])
        messages.addMessage("....{} changes made.".format(updates))
        messages.addMessage("End: Update.")


class Update_Bike_Model_03_Overlay_Attributes(object):  # pylint: disable=invalid-name
    """Updates overlay attributes on bike model/network, where automate-able.

    Bike_Facilities attributes updated by tool: inside_mpo, taz_num, ugb_code.
    """

    def __init__(self):
        """Initialize instance."""
        self.label = "Update Bike Model 03: Overlay Attributes"
        """str: How tool is named within toolbox."""
        self.category = None
        """str, NoneType: Name of sub-toolset tool will be in (optional)."""
        self.description = self.__class__.__doc__
        """str: Longer text describing tool, shown in side panel."""
        self.canRunInBackground = False
        """bool: Flag for whether tool controls ArcGIS focus while running."""

    def getParameterInfo(self):  # pylint: disable=no-self-use
        """Load parameters into toolbox.

        Recommended: Use `create_parameter` to allow initial
        definition to be a dictionary attribute map.

        Returns:
            list of arcpy.Parameter: Tool parameters.
        """
        parameters = [
            create_parameter(
                name="container_path",
                displayName="Bike Route Network Container",
                datatype="DEWorkspace",
                parameterType="Required",
            ),
            create_parameter(
                name="bike_facilities_path",
                displayName="Bike Facility Dataset",
                datatype="GPFeatureLayer",
                enabled=False,
                parameterType="Required",
            ),
            create_parameter(
                name="taz_path",
                displayName="Transportation Analysis Zone (TAZ) Dataset",
                datatype="GPFeatureLayer",
                parameterType="Required",
            ),
            create_parameter(
                name="taz_field_name",
                displayName="TAZ Number Field",
                datatype="Field",
                parameterType="Required",
                parameterDependencies=["taz_path"],
            ),
            create_parameter(
                name="ugb_path",
                displayName="Urban Growth Boundary (UGB) Dataset",
                datatype="GPFeatureLayer",
                parameterType="Required",
            ),
            create_parameter(
                name="ugb_field_name",
                displayName="UGB City Field",
                datatype="Field",
                parameterType="Required",
                parameterDependencies=["ugb_path"],
            ),
            create_parameter(
                name="mpo_path",
                displayName="Metropolitan Planning Organization (MPO) Boundary Dataset",
                datatype="GPFeatureLayer",
                parameterType="Required",
            ),
            create_parameter(
                name="mpo_field_name",
                displayName="MPO Flag Field",
                datatype="Field",
                parameterType="Required",
                parameterDependencies=["mpo_path"],
            ),
        ]
        return parameters

    def updateParameters(self, parameters):  # pylint: disable=no-self-use
        """Modify parameters before internal validation is performed.

        This method is called whenever a parameter has been changed.

        Args:
            parameters (list of arcpy.Parameter): Tool parameters.
        """
        parameter = {param.name: param for param in parameters}
        if parameter_changed(parameter["container_path"]) and arcpy.Exists(
            parameter_value(parameter["container_path"])
        ):
            # Attempt to find & assign dataset parameters.
            arcpy.env.workspace = parameter_value(parameter["container_path"])
            for parameter_name, dataset_name in [
                ("bike_facilities_path", "Bike_Facilities")
            ]:
                parameter[parameter_name].value = os.path.join(
                    arcpy.env.workspace, dataset_name
                )

    def execute(self, parameters, messages):  # pylint: disable=no-self-use
        """Execute tool procedure.

        Args:
            parameters (list of arcpy.Parameter): Tool parameters.
            messages (geoprocessing messages object): Tool messages.
        """
        value = parameter_value_map(parameters)
        # Uncomment loop to have info about parameter values in messages.
        # for param in parameters:
        #     messages.AddWarningMessage(param.name + " - " + param.datatype)
        #     messages.AddWarningMessage(value[param.name])
        arcpy.env.workspace = value["container_path"]
        for name in ["Bike_Facilities"]:
            if feature_count(name) == 0:
                messages.addErrorMessage(
                    "`{}` has no link features; ending run.".format(name)
                )
                return

        for key, link_key in [
            ("taz", "taz_num"),
            ("ugb", "ugb_code"),
            ("mpo", "inside_mpo"),
        ]:
            messages.addMessage("Start: Update {}.".format(link_key))
            updates = update_overlay(
                field_name=link_key,
                overlay_path=value[key + "_path"],
                overlay_field_name=value[key + "_field_name"],
                nonetype_replacement=("N" if key in ["mpo"] else None),
            )
            messages.addMessage("....{} changes made.".format(updates))
            messages.addMessage("End: Update.")


class Update_Bike_Model_04_Conflation_Attributes(object):  # pylint: disable=invalid-name
    """Updates conflation attributes on bike model/network, where automate-able.

    Bike_Facilities attributes updated by tool: fed_class, flcass.
    """

    def __init__(self):
        """Initialize instance."""
        self.label = "Update Bike Model 04: Conflation Attributes"
        """str: How tool is named within toolbox."""
        self.category = None
        """str, NoneType: Name of sub-toolset tool will be in (optional)."""
        self.description = self.__class__.__doc__
        """str: Longer text describing tool, shown in side panel."""
        self.canRunInBackground = False
        """bool: Flag for whether tool controls ArcGIS focus while running."""

    def getParameterInfo(self):  # pylint: disable=no-self-use
        """Load parameters into toolbox.

        Recommended: Use `create_parameter` to allow initial
        definition to be a dictionary attribute map.

        Returns:
            list of arcpy.Parameter: Tool parameters.
        """
        parameters = [
            create_parameter(
                name="container_path",
                displayName="Bike Route Network Container",
                datatype="DEWorkspace",
                parameterType="Required",
            ),
            create_parameter(
                name="bike_facilities_path",
                displayName="Bike Facility Dataset",
                datatype="GPFeatureLayer",
                enabled=False,
                parameterType="Required",
            ),
            create_parameter(
                name="roads_path",
                displayName="Road Dataset",
                datatype="GPFeatureLayer",
                parameterType="Required",
            ),
            create_parameter(
                name="fclass_field_name",
                displayName="(Local) Functional Class Field",
                datatype="Field",
                parameterDependencies=["roads_path"],
            ),
            create_parameter(
                name="fed_class_field_name",
                displayName="Federal Functional Class Field",
                datatype="Field",
                parameterDependencies=["roads_path"],
            ),
            create_parameter(
                name="max_distance",
                displayName="Maximum Conflation Distance",
                datatype="GPLinearUnit",
                parameterType="Required",
                value="16 Feet",
            ),
        ]
        return parameters

    def updateParameters(self, parameters):  # pylint: disable=no-self-use
        """Modify parameters before internal validation is performed.

        This method is called whenever a parameter has been changed.

        Args:
            parameters (list of arcpy.Parameter): Tool parameters.
        """
        parameter = {param.name: param for param in parameters}
        if parameter_changed(parameter["container_path"]) and arcpy.Exists(
            parameter_value(parameter["container_path"])
        ):
            # Attempt to find & assign dataset parameters.
            arcpy.env.workspace = parameter_value(parameter["container_path"])
            for parameter_name, dataset_name in [
                ("bike_facilities_path", "Bike_Facilities")
            ]:
                parameter[parameter_name].value = os.path.join(
                    arcpy.env.workspace, dataset_name
                )

    def execute(self, parameters, messages):  # pylint: disable=no-self-use
        """Execute tool procedure.

        Args:
            parameters (list of arcpy.Parameter): Tool parameters.
            messages (geoprocessing messages object): Tool messages.
        """
        value = parameter_value_map(parameters)
        # Uncomment loop to have info about parameter values in messages.
        # for param in parameters:
        #     messages.AddWarningMessage(param.name + " - " + param.datatype)
        #     messages.AddWarningMessage(value[param.name])
        arcpy.env.workspace = value["container_path"]
        for name in ["Bike_Facilities"]:
            if feature_count(name) == 0:
                messages.addErrorMessage(
                    "`{}` has no link features; ending run.".format(name)
                )
                return

        meta = {"road_sref": arcpy.Describe(value["roads_path"]).spatialReference}
        messages.addMessage(
            "Start: Collect attributes from roads"
            + " of matching name & geometric configuration."
        )
        temp_links_path = unique_path("links_")
        arcpy.management.CreateFeatureclass(
            out_path=os.path.dirname(temp_links_path),
            out_name=os.path.basename(temp_links_path),
            geometry_type="polyline",
            # template="Bike_Facilities",
            spatial_reference=meta["road_sref"],
        )
        field = arcpy.ListFields("Bike_Facilities", "link_id")[0]
        arcpy.management.AddField(
            temp_links_path,
            field.name,
            field.type,
            field.precision,
            field.scale,
            field.length,
        )
        arcpy.management.Append(
            inputs="Bike_Facilities", target=temp_links_path, schema_type="no_test"
        )
        xfer_kwargs = {
            "source_features": value["roads_path"],
            "target_features": temp_links_path,
            "transfer_fields": [
                value[key]
                for key in ["fclass_field_name", "fed_class_field_name"]
                if value[key]
            ],
            "search_distance": value["max_distance"],
        }
        if not xfer_kwargs["transfer_fields"]:
            raise arcpy.ExecuteError("No fields chosen for conflation.")

        arcpy.edit.TransferAttributes(**xfer_kwargs)
        cursor = arcpy.da.SearchCursor(
            in_table=temp_links_path,
            field_names=(["link_id"] + xfer_kwargs["transfer_fields"]),
        )
        link_class = {}
        with cursor:
            for row in cursor:
                link_id = row[0]
                link_class[link_id] = {}
                if value["fclass_field_name"]:
                    link_class[link_id]["fclass"] = row[1]
                if len(row) == 3:
                    link_class[link_id]["fed_class"] = row[2]
                else:
                    link_class[link_id]["fed_class"] = row[1]
        arcpy.management.Delete(temp_links_path)
        messages.addMessage("End: Collect.")
        updated_count = 0
        cursor = arcpy.da.UpdateCursor(
            "Bike_Facilities", field_names=["link_id", "fclass", "fed_class"]
        )
        with cursor:
            for link_id, old_fclass, old_fed_class in cursor:
                new_fclass = (
                    link_class[link_id]["fclass"]
                    if value["fclass_field_name"]
                    else old_fclass
                )
                new_fclass = "" if new_fclass is None else new_fclass
                new_fed_class = (
                    link_class[link_id]["fed_class"]
                    if value["fed_class_field_name"]
                    else old_fed_class
                )
                new_fed_class = "" if new_fed_class is None else new_fed_class
                if old_fclass != new_fclass or old_fed_class != new_fed_class:
                    cursor.updateRow([link_id, new_fclass, new_fed_class])
                    updated_count += 1
                    for attr_key, old_val, new_val in [
                        ("fclass", old_fclass, new_fclass),
                        ("fed_class", old_fed_class, new_fed_class),
                    ]:
                        if old_val != new_val:
                            arcpy.AddMessage(
                                describe_attribute_change(
                                    attr_key,
                                    new_val,
                                    feature_id_key="link_id",
                                    feature_id_value=link_id,
                                    old_attribute_value=old_val,
                                )
                            )
        messages.addMessage("....{} changes made.".format(updated_count))
        messages.addMessage("End: Update.")


# Tool-specific objects.


def clean_attributes(field_name, cleaner):
    """Clean attributes in a dataset"s field with the listed "cleaners".

    Args:
        field_name (str): Name of the field with the attributes.
        cleaner (types.FunctionType, list, tuple): Cleaner to apply to the
            attribute field. If a collection, function will be recursively called.

    Returns:
        int: Count of updates that occurred.
    """
    updated_count = 0
    if isinstance(cleaner, (list, tuple)):
        for a_cleaner in cleaner:
            updated_count += clean_attributes(field_name, a_cleaner)
    elif isinstance(cleaner, FunctionType):
        cursor = arcpy.da.UpdateCursor(
            "Bike_Facilities", field_names=["link_id", field_name]
        )
        with cursor:
            for link_id, old_val in cursor:
                new_val = cleaner(old_val)
                if old_val != new_val:
                    cursor.updateRow([link_id, new_val])
                    updated_count += 1
                    arcpy.AddMessage(
                        describe_attribute_change(
                            field_name,
                            new_val,
                            feature_id_key="link_id",
                            feature_id_value=link_id,
                            old_attribute_value=old_val,
                        )
                    )
    else:
        raise TypeError("`cleaner` type must be list, tuple, or function.")

    return updated_count


def update_bearings():
    """Update line-end bearings on the bike facility links.

    Returns:
        int: Count of updates that occurred.
    """
    updated_count = 0
    cursor = arcpy.da.UpdateCursor(
        "Bike_Facilities", field_names=["link_id", "fbearing", "tbearing", "shape@"]
    )
    with cursor:
        for link_id, old_fbearing, old_tbearing, geom in cursor:
            new_fbearing = int(round(line_end_bearing(geom, "from")))
            new_tbearing = int(round(line_end_bearing(geom, "to")))
            if old_fbearing != new_fbearing or old_tbearing != new_tbearing:
                cursor.updateRow([link_id, new_fbearing, new_tbearing, geom])
                updated_count += 1
                for attr_key, old_val, new_val in [
                    ("fbearing", old_fbearing, new_fbearing),
                    ("tbearing", old_tbearing, new_tbearing),
                ]:
                    if old_val != new_val:
                        arcpy.AddMessage(
                            describe_attribute_change(
                                attr_key,
                                new_val,
                                feature_id_key="link_id",
                                feature_id_value=link_id,
                                old_attribute_value=old_val,
                            )
                        )
    return updated_count


def update_distances():
    """Update distance attribute on the bike facility links.

    Returns:
        int: Count of updates that occurred.
    """
    updated_count = 0
    cursor = arcpy.da.UpdateCursor(
        "Bike_Facilities", field_names=["link_id", "dst_ft", "shape@"]
    )
    with cursor:
        for link_id, old_dist, geom in cursor:
            new_dist = geom.length
            if old_dist is None or abs(old_dist - new_dist) > 0.01:
                cursor.updateRow([link_id, new_dist, geom])
                updated_count += 1
                arcpy.AddMessage(
                    describe_attribute_change(
                        "dst_ft",
                        new_dist,
                        feature_id_key="link_id",
                        feature_id_value=link_id,
                        old_attribute_value=old_dist,
                    )
                )
    return updated_count


def update_elevation_deltas(surface_path):
    """Update elevation deltas on the bike facility links.

     Deltas:
        dzp: Delta elevation gain.
        dzn: Delta elevation loss.

     Creates a temporary in-memory 3D version of the bike facility links referenced
     against an elevation model. ArcGIS 3D Analyst extension is required.

    Args:
        surface_path (str): Path to the surface to derive elevation from.

    Returns:
        int: Count of updates that occurred.
    """
    # Grab 3D Analyst as soon as possible.
    if arcpy.CheckOutExtension("3D") == "CheckedOut":
        arcpy.AddMessage("Checked-out ArcGIS 3D Analyst extension.")
    else:
        raise LicenseError("Cannot check out ArcGIS 3D Analyst extension.")

    # Create temporary dataset with the identity values.
    temp_links_path = unique_path(prefix="links_", suffix="_3D")
    # # Derive elevation values along all links.
    arcpy.InterpolateShape_3d(
        in_surface=surface_path,
        in_feature_class="Bike_Facilities",
        out_feature_class=temp_links_path,
    )
    # Check back in the extension, we"re done with it.
    arcpy.CheckInExtension("3D")
    updated_count = 0
    cursor = arcpy.da.SearchCursor(temp_links_path, field_names=["link_id", "shape@"])
    with cursor:
        link_dzs = {}
        for link_id, geom in cursor:
            deltas = list(elevation_deltas(geom))
            try:
                link_dzs[link_id] = {
                    "dzp": int(round(abs(sum(d for d in deltas if d > 0)))),
                    "dzn": int(round(abs(sum([d for d in deltas if d < 0])))),
                }
            # This shouldn"t happen since Z-coordinates are added in tool.
            except ValueError:
                arcpy.AddError("`link_id`={}: Missing Z-coordinates.".format(link_id))
                raise

    arcpy.management.Delete(temp_links_path)
    cursor = arcpy.da.UpdateCursor(
        "Bike_Facilities", field_names=["link_id", "dzp", "dzn"]
    )
    with cursor:
        for link_id, old_dzp, old_dzn in cursor:
            if link_id in link_dzs:
                new_dzp = link_dzs[link_id]["dzp"]
                new_dzn = link_dzs[link_id]["dzn"]
            else:
                new_dzp = new_dzn = None
            if old_dzp != new_dzp or old_dzn != new_dzn:
                cursor.updateRow([link_id, new_dzp, new_dzn])
                updated_count += 1
                for attr_key, old_val, new_val in [
                    ("dzp", old_dzp, new_dzp),
                    ("dzn", old_dzn, new_dzn),
                ]:
                    if old_val != new_val:
                        arcpy.AddMessage(
                            describe_attribute_change(
                                attr_key,
                                new_val,
                                feature_id_key="link_id",
                                feature_id_value=link_id,
                                old_attribute_value=old_val,
                            )
                        )
    return updated_count


def update_descriptions():
    """Update line-end bearings on the bike facility links.

    Returns:
        int: Count of updates that occurred.
    """
    cursor = arcpy.da.SearchCursor(
        "Valid_Functional_Class", field_names=["code", "description"]
    )
    with cursor:
        code_description = {code: description for code, description in cursor}
    updated_count = 0
    cursor = arcpy.da.UpdateCursor(
        "Bike_Facilities", field_names=["link_id", "fc_code", "fc_desc"]
    )
    with cursor:
        for link_id, code, old_description in cursor:
            new_description = code_description.get(code)
            if old_description != new_description:
                cursor.updateRow([link_id, code, new_description])
                updated_count += 1
                arcpy.AddMessage(
                    describe_attribute_change(
                        "fc_desc",
                        new_description,
                        feature_id_key="link_id",
                        feature_id_value=link_id,
                        old_attribute_value=old_description,
                    )
                )
    return updated_count


def update_link_ids():
    """Update link IDs on the bike facilities.

    A new link_id will be assigned in the following cases:
        Case 1: A link has the same ID as another link.
            Reason 1: Links were parts of a previous link that was split.
            Reason 2: Link had its ID manually-entered improperly.
        Case 2: A link"s"s ID-value is missing.
            Reason 1: Link is new to the dataset.
            Reason 2: Link had its previous ID manually-removed (this will look to
                the function like a new feature).

    Returns:
        collections.Counter: Counts for types of updates that occurred.
    """
    updated_count = Counter({"new": 0, "changed": 0})
    cursor = arcpy.da.SearchCursor("Bike_Facilities", field_names=["link_id"])
    with cursor:
        try:
            next_id = max(link_id for link_id, in cursor) + 1
        # TypeError = All ID values are NoneType.
        except TypeError:
            next_id = 1
    new_old_link_id = {}
    cursor = arcpy.da.UpdateCursor("Bike_Facilities", field_names=["oid@", "link_id"])
    with cursor:
        link_oid = {}
        for oid, old_link_id in cursor:
            # "Prime" occurance of feature_id: Note & keep ID.
            if old_link_id is not None and old_link_id not in link_oid:
                link_oid[old_link_id] = oid
            # Missing or duplicate ID (i.e. on a non-prime) feature ID: Assign new ID.
            else:
                cursor.updateRow([oid, next_id])
                if old_link_id is not None:
                    new_old_link_id[next_id] = old_link_id
                    updated_count["changed"] += 1
                else:
                    updated_count["new"] += 1
                arcpy.AddMessage(
                    describe_attribute_change(
                        attribute_key="link_id",
                        new_attribute_value=next_id,
                        feature_id_key="OID",
                        feature_id_value=oid,
                        old_attribute_value=old_link_id,
                    )
                )
                next_id += 1
    # Write the ID changes to the transformations table.
    cursor = arcpy.da.InsertCursor(
        "Link_ID_Transforms", field_names=["new_link_id", "old_link_id"]
    )
    with cursor:
        for row in new_old_link_id.items():
            cursor.insertRow(row)
    return updated_count


def update_node_ids():
    """Update node IDs on the bike facilities.

    Returns:
        int: Count of updates that occurred.
    """
    coord_node = coordinate_node_map(
        "Bike_Facilities", "fnode", "tnode", "link_id", update_node_ids=True
    )
    # link_node format: {link_id: {"fnode": int(), "tnode": int()}}
    link_node = defaultdict(dict)
    for node in coord_node.values():
        for end, field_key in [("from", "fnode"), ("to", "tnode")]:
            for link_id in node["ids"][end]:
                link_node[link_id][field_key] = node["node_id"]
    updated_count = 0
    cursor = arcpy.da.UpdateCursor(
        "Bike_Facilities", field_names=["link_id", "fnode", "tnode"]
    )
    with cursor:
        for link_id, old_fnode, old_tnode in cursor:
            new_fnode = link_node[link_id]["fnode"]
            new_tnode = link_node[link_id]["tnode"]
            if old_fnode != new_fnode or old_tnode != new_tnode:
                cursor.updateRow([link_id, new_fnode, new_tnode])
                updated_count += 1
                for attr_key, old_val, new_val in [
                    ("fnode", old_fnode, new_fnode),
                    ("tnode", old_tnode, new_tnode),
                ]:
                    if old_val != new_val:
                        arcpy.AddMessage(
                            describe_attribute_change(
                                attr_key,
                                new_val,
                                feature_id_key="link_id",
                                feature_id_value=link_id,
                                old_attribute_value=old_val,
                            )
                        )
    return updated_count


def update_nodes():
    """Update nodes dataset.

    Returns:
        collections.Counter: Counts for types of updates that occurred.
    """
    coord_node = coordinate_node_map("Bike_Facilities", "fnode", "tnode", "link_id")
    updated_count = Counter({"new": 0, "changed": 0})
    cursor = arcpy.da.UpdateCursor("Nodes", field_names=["oid@", "shape@xy", "node_id"])
    with cursor:
        for oid, coord, old_node_id in cursor:
            # If node location not in node_info: delete.
            if coord not in coord_node:
                cursor.deleteRow()
                arcpy.AddMessage(
                    "Deleted `OID`={} at coordinate={}.".format(oid, coord)
                )
                continue

            # Pops will reduce coord_node to only ones to add (later).
            new_node_id = coord_node.pop(coord)["node_id"]
            if old_node_id != new_node_id:
                cursor.updateRow([oid, coord, new_node_id])
                updated_count["changed"] += 1
                arcpy.AddMessage(
                    describe_attribute_change(
                        "node_id",
                        new_node_id,
                        feature_id_key="OID",
                        feature_id_value=oid,
                        old_attribute_value=old_node_id,
                    )
                )
    # Now add new nodes.
    cursor = arcpy.da.InsertCursor("Nodes", field_names=["node_id", "shape@xy"])
    with cursor:
        for coord, node in coord_node.items():
            cursor.insertRow([node["node_id"], coord])
            updated_count["new"] += 1
            arcpy.AddMessage(
                "Added `node_id`={} at coordinate={}.".format(node["node_id"], coord)
            )
    return updated_count


def update_overlay(
    field_name, overlay_path, overlay_field_name, nonetype_replacement=None
):
    """Update overlay attribute on the bike facility links.

    The overlay attribute will use the value of the "dominant" feature(s) that overlay
    more of the link than any other.

    Args:
        bike_facility_path (str): Path to the bike facility dataset.
        surface_path (str): Path to the surface to derive elevation from.
    """
    temp_overlay_path = unique_path("overlay_")
    arcpy.management.CopyFeatures(overlay_path, out_feature_class=temp_overlay_path)
    temp_overlay_field_name = unique_name("overlay_")
    arcpy.management.AlterField(
        temp_overlay_path,
        field=overlay_field_name,
        new_field_name=temp_overlay_field_name,
    )
    temp_out_path = unique_path("out_")
    arcpy.analysis.Identity(
        "Bike_Facilities",
        identity_features=temp_overlay_path,
        out_feature_class=temp_out_path,
        join_attributes="all",
    )
    arcpy.management.Delete(temp_overlay_path)
    cursor = arcpy.da.SearchCursor(
        temp_out_path, field_names=["link_id", temp_overlay_field_name, "shape@length"]
    )
    with cursor:
        # link_overlay format: {link_id: Counter({overlay: length})}
        link_overlay = defaultdict(Counter)
        for link_id, overlay, length in cursor:
            link_overlay[link_id][overlay] += length
    arcpy.management.Delete(temp_out_path)
    updated_count = 0
    cursor = arcpy.da.UpdateCursor(
        "Bike_Facilities", field_names=["link_id", field_name]
    )
    with cursor:
        for link_id, old_overlay in cursor:
            new_overlay = max(link_overlay[link_id])
            if new_overlay in [None, ""] and nonetype_replacement is not None:
                new_overlay = nonetype_replacement
            if old_overlay != new_overlay:
                cursor.updateRow([link_id, new_overlay])
                updated_count += 1
                arcpy.AddMessage(
                    describe_attribute_change(
                        field_name,
                        new_overlay,
                        feature_id_key="link_id",
                        feature_id_value=link_id,
                        old_attribute_value=old_overlay,
                    )
                )
    return updated_count


def update_taz_links():
    """Update TAZ-node links."""
    cursor = arcpy.da.SearchCursor("TAZ_Centroids", field_names=["taz_id", "shape@xy"])
    with cursor:
        taz_info = {
            taz_id: {"link_coords": [coord], "taz_node_id": None}
            for taz_id, coord in cursor
        }
    cursor = arcpy.da.SearchCursor(
        "TAZ_Nodes", field_names=["taz_id", "taz_node_id", "shape@xy"]
    )
    with cursor:
        for taz_id, taz_node_id, coord in cursor:
            if taz_id in taz_info:
                taz_info[taz_id]["taz_node_id"] = taz_node_id
                if coord == taz_info[taz_id]["link_coords"][0]:
                    taz_info[taz_id]["link_coords"].append((coord[0] + 1, coord[1] + 1))
                taz_info[taz_id]["link_coords"].append(coord)
    updated_count = Counter({"new": 0, "changed": 0})
    cursor = arcpy.da.UpdateCursor(
        "TAZ_Links", field_names=["taz_id", "taz_node_id", "shape@"]
    )
    for taz_id, taz in taz_info.items():
        if len(taz["link_coords"]) < 2:
            taz["link_geom"] = None
            arcpy.AddWarning(
                "`taz_id`={} not found in `TAZ_Nodes`;".format(taz_id)
                + " setting link geometry=None."
            )
        else:
            taz["link_geom"] = arcpy.Polyline(
                arcpy.Array(arcpy.Point(*coord) for coord in taz["link_coords"])
            )
    with cursor:
        for taz_id, old_taz_node_id, old_geom in cursor:
            if taz_id not in taz_info:
                cursor.deleteRow()
                arcpy.AddMessage(
                    "Deleted `taz_id`={} (TAZ does not exist).".format(taz_id)
                )
                continue

            # Pops will reduce taz_info to only ones to add.
            taz = taz_info.pop(taz_id)
            new_taz_node_id = taz["taz_node_id"]
            new_geom = taz["link_geom"]
            if old_taz_node_id != new_taz_node_id or not old_geom.equals(new_geom):
                cursor.updateRow([taz_id, new_taz_node_id, new_geom])
                updated_count["changed"] += 1
                for attr_key, old_val, new_val in [
                    ("taz_node_id", old_taz_node_id, new_taz_node_id),
                    # ("geometry", old_geom, new_geom)
                ]:
                    if old_val != new_val:
                        arcpy.AddMessage(
                            describe_attribute_change(
                                attr_key,
                                new_val,
                                feature_id_key="taz_id",
                                feature_id_value=taz_id,
                                old_attribute_value=old_val,
                            )
                        )
    # Now add new links.
    cursor = arcpy.da.InsertCursor(
        "TAZ_Links", field_names=["taz_id", "taz_node_id", "shape@"]
    )
    with cursor:
        for taz_id, taz in taz_info.items():
            cursor.insertRow((taz_id, taz["taz_node_id"], taz["link_geom"]))
            updated_count["new"] += 1
            arcpy.AddMessage("Added new link for `taz_id`={}.".format(taz_id))
    return updated_count


def update_taz_nodes(search_distance):
    """Update TAZ-nodes.

    These nodes were generally settled-upon as representative of their related TAZ for
    model usage. The updates here are for when nodes move and/or change IDs.

    Args:
        search_distance (int, float, str): Furthest distance an associated node can be
            from the previous TAZ-node location. Numeric values will be in units of the
            coordinate system.

    Returns:
        int: Count of updates that occurred.
    """
    cursor = arcpy.da.SearchCursor("TAZ_Centroids", field_names=["taz_id"])
    with cursor:
        valid_taz_ids = {taz_id for taz_id, in cursor}
    # Spatially join TAZ-nodes to the nodes with new IDs.
    temp_path = unique_path("taz_node_")
    arcpy.analysis.SpatialJoin(
        target_features="TAZ_Nodes",
        join_features="Nodes",
        out_feature_class=temp_path,
        match_option="closest",
        search_radius=search_distance,
    )
    updated_count = 0
    cursor = arcpy.da.SearchCursor(temp_path, field_names=["taz_id", "node_id"])
    with cursor:
        taz_node_id = {taz_id: node_id for taz_id, node_id in cursor}
    arcpy.management.Delete(temp_path)
    if valid_taz_ids - set(taz_node_id):
        # arcpy.AddError("Not all TAZ represented in `TAZ_Nodes`!")
        # return updated_count
        raise arcpy.ExecuteError("Not all TAZ represented in `TAZ_Nodes`!")

    # Get node coordinates.
    cursor = arcpy.da.SearchCursor("Nodes", field_names=["node_id", "shape@xy"])
    with cursor:
        node_coord = {node_id: coord for node_id, coord in cursor}
    # Push new TAZ-node values to the dataset.
    cursor = arcpy.da.UpdateCursor(
        "TAZ_Nodes", field_names=["taz_id", "taz_node_id", "shape@xy"]
    )
    with cursor:
        for taz_id, old_node_id, old_coord in cursor:
            if taz_id not in valid_taz_ids:
                cursor.deleteRow()
                arcpy.AddMessage(
                    "Deleted `taz_id`={} (TAZ does not exist).".format(taz_id)
                )
                continue

            new_node_id = taz_node_id[taz_id]
            new_coord = node_coord[new_node_id]
            # Do not change XY if missing (no node found in the search radius).
            if new_coord is None:
                new_coord = old_coord
                arcpy.AddWarning(
                    (
                        "No current nodes found within {} of old node for"
                        + " `taz_id`={}. Setting `taz_node_id`=None."
                    ).format(search_distance, taz_id)
                )
            if old_node_id != new_node_id or old_coord != new_coord:
                cursor.updateRow([taz_id, new_node_id, new_coord])
                updated_count += 1
                for attr_key, old_val, new_val in [
                    ("taz_node_id", old_node_id, new_node_id),
                    ("shape@xy", old_coord, new_coord),
                ]:
                    if old_val != new_val:
                        arcpy.AddMessage(
                            describe_attribute_change(
                                attr_key,
                                new_val,
                                feature_id_key="taz_id",
                                feature_id_value=taz_id,
                                old_attribute_value=old_val,
                            )
                        )
    return updated_count


# Geoprocessing objects.


def _node_feature_count(node):
    """Return feature count for node from its info map.

    Args:
        node (dict): Node information.

    Returns:
        int: Number of nodes.
    """
    return len(node["ids"]["from"].union(node["ids"]["to"]))


def _update_coord_node_map(coord_node, node_id_field):
    """Return updated coordinate node info map."""
    coord_node = copy.deepcopy(coord_node)
    used_ids = {
        node["node_id"] for node in coord_node.values() if node["node_id"] is not None
    }
    unused_ids = (
        _id
        for _id in unique_ids(
            python_type(node_id_field.type), string_length=node_id_field.length
        )
        if _id not in used_ids
    )
    id_coords = {}
    for coord, node in coord_node.items():
        # Assign IDs where missing.
        if node["node_id"] is None:
            node["node_id"] = next(unused_ids)
        # If ID duplicate, re-ID node with least features.
        elif node["node_id"] in id_coords:
            other_coord = id_coords[node["node_id"]]
            other_node = coord_node[other_coord]
            new_node_id = next(unused_ids)
            if _node_feature_count(node) > _node_feature_count(other_node):
                other_node["node_id"] = new_node_id  # Does update coord_node!
                id_coords[new_node_id] = id_coords.pop(node["node_id"])
            else:
                node["node_id"] = new_node_id  # Does update coord_node!
        id_coords[node["node_id"]] = coord
    return coord_node


def coordinate_node_map(dataset_path, from_key, to_key, id_key="oid@", **kwargs):
    """Return dictionary mapping of coordinates to node-info dictionaries.

    Note: From & to IDs must be the same attribute type.

    Args:
        dataset_path (str): Path of the dataset.
        from_key (str): Name of the from-ID field.
        to_key (str): Name of the to-ID field.
        id_key (str): Name of the ID field. Default is "oid@".
        **kwargs: Arbitrary keyword arguments. See below.

    Keyword Args:
        dataset_where_sql (str): SQL where-clause for dataset subselection.
        update_node_ids (bool): Flag to indicate whether to update nodes based on feature
            geometries. Default is False.

    Returns:
        dict: Mapping of coordinate tuples to node-info dictionaries.
            `{(x, y): {"node_id": <id>, "ids": {"from": set(), "to": set()}}}`
    """
    kwargs.setdefault("dataset_where_sql")
    kwargs.setdefault("update_node_ids", False)
    keys = {"field": (id_key, from_key, to_key, "shape@")}
    coord_node = {}
    cursor = arcpy.da.SearchCursor(
        dataset_path, keys["field"], where_clause=kwargs["dataset_where_sql"]
    )
    with cursor:
        for feature_id, from_node_id, to_node_id, geom in cursor:
            for end, node_id, point in [
                ("from", from_node_id, geom.firstPoint),
                ("to", to_node_id, geom.lastPoint),
            ]:
                coord = (point.X, point.Y)
                if coord not in coord_node:
                    # Create new coordinate-node.
                    coord_node[coord] = {"node_id": node_id, "ids": defaultdict(set)}
                # Assign new ID if current is missing, or lower than current.
                coord_node[coord]["node_id"] = (
                    node_id
                    if coord_node[coord]["node_id"] is None
                    else min(coord_node[coord]["node_id"], node_id)
                )
                # Add feature ID to end-ID set.
                coord_node[coord]["ids"][end].add(feature_id)
    if kwargs["update_node_ids"]:
        node_id_field = arcpy.ListFields(dataset_path, wild_card=from_key)[0]
        coord_node = _update_coord_node_map(coord_node, node_id_field)
    return coord_node


def elevation_deltas(geometry):
    """Generate elevation deltas for the given geometry.

    Args:
        geometry (arcpy.Polyline): Line geometry to get deltas of.

    Yields:
        float: Change in elevation (delta) between vertices in the geometry.
    """
    for z_0, z_1 in pairwise(point.Z for array in geometry for point in array):
        if z_0 is None or z_1 is None:
            raise ValueError("Missing z-value in geometry vertex.")

        yield z_1 - z_0


def get_bearing(coord0, coord1):
    """Find directional bearing or angle of input coordinates.

    Args:
        coord0 (iter): Two-part iterable of an X- & Y-value for the first point.
        coord0 (iter): Two-part iterable of an X- & Y-value for the second point.

    Returns:
        float: Directional bearing for the coordinates.
    """
    if coord0 == coord1:
        raise ValueError("Coordinates are the same point.")

    x_0, y_0 = coord0
    x_1, y_1 = coord1
    run = x_1 - x_0
    rise = y_1 - y_0
    try:
        theta_angle = math.degrees(math.atan(abs(run / rise)))
    except ZeroDivisionError:
        theta_angle = None
    if theta_angle is None:
        # Bearing is either 90 or 270 (cannot divide by zero for equation).
        bearing = 90 if run > 0 else 270
    else:
        # Top-right quadrant (0-89.x).
        if run >= 0 and rise > 0:
            bearing = theta_angle
        # Lower-right quadrant (90.x-180).
        if run >= 0 and rise < 0:
            bearing = 180 - theta_angle
        # Lower-left quadrant (180-269.x). Don"t care about 180 overlap.
        if run <= 0 and rise < 0:
            bearing = 180 + theta_angle
        # Top-left quadrant (270.x-359.x). Do care about 360 overlap 0.
        if run < 0 and rise > 0:
            bearing = 360 - theta_angle
    return bearing


def line_end_bearing(geometry, end):
    """Determine the line-end bearings for given polyline geometry.

    Args:
        geometry (arcpy.Polyline): Line geometry to evaluate.
        end (str): Indicator of whether "from" or "to" end bearing is determined.

    Returns:
        float: Directional bearing for the line-end.
    """
    if end.lower() == "from":
        linepart = geometry.getPart(0)
    elif end.lower() == "to":
        linepart = geometry.getPart(geometry.partCount - 1)
    else:
        raise AttributeError("""`end` argument must be "from" or "to\"""")

    point_count = linepart.count
    if end.lower() == "from":
        i = 0
        coord0 = coord1 = (linepart.getObject(i).X, linepart.getObject(i).Y)
        while coord1 == coord0:
            i += 1
            coord1 = (linepart.getObject(i).X, linepart.getObject(i).Y)
    elif end.lower() == "to":
        i = point_count - 1
        coord1 = coord0 = (linepart.getObject(i).X, linepart.getObject(i).Y)
        while coord1 == coord0:
            i -= 1
            coord0 = (linepart.getObject(i).X, linepart.getObject(i).Y)
    return get_bearing(coord0, coord1)


# Utility objects.


def clean_whitespace(value, clear_empty_string=True):
    """Return value with whitespace stripped & deduplicated.

    Args:
        value (str): Value to clean.
        clear_empty_string (bool): Convert empty string results to NoneTypes if True.

    Returns
        str, NoneType: Cleaned value.
    """
    if value is not None:
        value = value.strip()
        for character in string.whitespace:
            while character * 2 in value:
                value = value.replace(character * 2, character)
    if clear_empty_string and not value:
        value = None
    return value


def describe_attribute_change(attribute_key, new_attribute_value, **kwargs):
    """Return description of an attribute change (useful for logging).

    Args:
        attribute_key (str): Name of the attribute.
        new_attribute_value: New value of the attribute.
        **kwargs: Arbitrary keyword arguments. See below.

    Keyword Args:
        feature_id_key (str): Name of the feature ID attribute.
        feature_id_value: Value of the feature ID.
        old_attribute_value: Old value of the attribute.

    Returns:
        str: Change description.
    """
    desc = "Changed {}=".format(attribute_key)
    if "old_attribute_value" in kwargs:
        desc += "{old_attribute_value!r} --> ".format(**kwargs)
    desc += "{!r}".format(new_attribute_value)
    if "feature_id_key" in kwargs:
        kwargs.setdefault("feature_id_value")
        desc += " for {feature_id_key}={feature_id_value!r}".format(**kwargs)
    desc += "."
    return desc


def feature_count(dataset_path):
    """Number of features in dataset.

    Args:
        dataset_path (str): Path of the dataset.

    Returns:
        int: Number of features in the view.
    """
    return int(arcpy.management.GetCount(dataset_path).getOutput(0))


def pairwise(iterable):
    """Generate overlapping ordered pairs from an iterable.

    i -> (i[0], i[1]), (i[1], i[2]), (i[2], i[3]), ...

    Args:
        iterable (iter): Iterable to walk.

    Yields:
        tuple: Pair from iterable.
    """
    pair = {}
    for x in iterable:
        if 0 not in pair:
            pair[0] = x
            continue

        pair[1] = x
        yield (pair[0], pair[1])
        pair[0] = pair[1]


def python_type(type_description):
    """Return object representing the Python type.

    Args:
        type_description (str): Arc-style type description/code.

    Returns:
        Python object representing the type.
    """
    instance = {
        "date": datetime.datetime,
        "double": float,
        "single": float,
        "integer": int,
        "long": int,
        "short": int,
        "smallinteger": int,
        "geometry": arcpy.Geometry,
        "guid": uuid.UUID,
        "string": str,
        "text": str,
    }
    return instance[type_description.lower()]


def unique_ids(data_type=uuid.UUID, string_length=4):
    """Generate unique IDs.

    Args:
        data_type: Type object to create unique IDs as.
        string_length (int): Length to make unique IDs of type string. Ignored if
            data_type is not a string type.

    Yields:
        Unique ID.
    """
    if data_type in [float, int]:
        # Skip 0 (problematic - some processing functions use 0 for null).
        unique_id = data_type(1)
        while True:
            yield unique_id

            unique_id += 1
    elif data_type in [uuid.UUID]:
        while True:
            yield uuid.uuid4()

    elif data_type in [str]:
        seed = string.ascii_letters + string.digits
        used_ids = set()
        while True:
            unique_id = "".join(random.choice(seed) for _ in range(string_length))
            if unique_id in used_ids:
                continue

            yield unique_id

            used_ids.add(unique_id)
    else:
        raise NotImplementedError(
            "Unique IDs for {} type not implemented.".format(data_type)
        )


def unique_name(prefix="", suffix="", unique_length=4, allow_initial_digit=True):
    """Generate unique name.

    Args:
        prefix (str): String to insert before the unique part of the name.
        suffix (str): String to append after the unique part of the name.
        unique_length (int): Number of unique characters to generate.
        allow_initial_number (bool): Flag indicating whether to let the initial
            character be a number. Default is True.

    Returns:
        str: Unique name.
    """
    name = prefix + next(unique_ids(str, unique_length)) + suffix
    if not allow_initial_digit and name[0].isdigit():
        name = unique_name(prefix, suffix, unique_length, allow_initial_digit)
    return name


def unique_path(prefix="", suffix="", unique_length=4, workspace_path="in_memory"):
    """Create unique temporary dataset path.

    Args:
        prefix (str): String to insert before the unique part of the name.
        suffix (str): String to append after the unique part of the name.
        unique_length (int): Number of unique characters to generate.
        workspace_path (str): Path of workspace to create the dataset in.

    Returns:
        str: Path of the created dataset.
    """
    name = unique_name(prefix, suffix, unique_length, allow_initial_digit=False)
    return os.path.join(workspace_path, name)


# General toolbox objects.


def create_parameter(name, **kwargs):
    """Create ArcPy parameter object using an attribute mapping.

    Note that this doesn"t check if the attribute exists in the default
    parameter instance. This means that you can attempt to set a new
    attribute, but the result will depend on how the class implements setattr
    (usually this will just attach the new attribute).

    Args:
        name (str): Internal reference name for parameter (required).

    Keyword Args:
        displayName (str): Label as shown in tool"s dialog. Default is parameter name.
        direction (str): Direction of the parameter: Input or Output. Default is Input.
        datatype (str): Parameter data type. Default is GPVariant. See
            https://desktop.arcgis.com/en/arcmap/latest/analyze/creating-tools/defining-parameter-data-types-in-a-python-toolbox.htm
        parameterType (str): Parameter type: Optional, Required, or Derived. Default is
            Optional.
        enabled (bool): Flag to set parameter as enabled or disabled. Default is True.
        category (str, NoneType): Category to include parameter in. Naming a category
            will hide tool in collapsed category on open. Set to None (the default) for
            tool to be at top-level.
        symbology (str, NoneType): Path to layer file used for drawing output. Set to
            None (the default) to omit symbology.
        multiValue (bool): Flag to set whether parameter is multi-valued. Default is
            False.
        value (object): Data value of the parameter. Object"s type must be the Python
            equivalent of parameter "datatype". Default is None.
        columns (list of list): Ordered collection of data type/name pairs for value
            table columns. Ex: `[["GPFeatureLayer", "Features"], ["GPLong", "Ranks"]]`
        filter_type (str): Type of filter to apply: ValueList, Range, FeatureClass,
            File, Field, or Workspace.
        filter_list (list): Collection of possible values allowed by the filter type.
            Default is an empty list.
        parameterDependencies (list): Collection other parameter"s names that this
            parameter"s value depends upon.

    Returns:
        arcpy.Parameter: Parameter derived from the attributes.

    """
    kwargs.setdefault("displayName", name)
    kwargs.setdefault("direction", "Input")
    kwargs.setdefault("datatype", "GPVariant")
    kwargs.setdefault("parameterType", "Optional")
    kwargs.setdefault("enabled", True)
    kwargs.setdefault("category")
    kwargs.setdefault("symbology")
    kwargs.setdefault("multiValue", False)
    kwargs.setdefault("value")
    # DO NOT SET DEFAULT: kwargs.setdefault("filter_list", [])
    # DO NOT SET DEFAULT: kwargs.setdefault("parameterDependencies", [])
    parameter = arcpy.Parameter(name)
    for attr, value in kwargs.items():
        # Apply filter properties later.
        if attr.startswith("filter_"):
            continue
        else:
            setattr(parameter, attr, value)
    for key in ["filter_type", "filter_list"]:
        if key in kwargs:
            setattr(parameter.filter, key.replace("filter_", ""), kwargs[key])
    return parameter


def parameter_changed(parameter):
    """Check whether parameter is in a pre-validation changed state.

    Args:
        arcpy.Parameter: Parameter to check.

    Returns:
        bool: True if changed, False otherwise.
    """
    return all((parameter.altered, not parameter.hasBeenValidated))


def parameter_value(parameter):
    """Get current parameter value.

    If value attribute references a geoprocessing value object, will use this
    function recursively to get the actual value.

    Args:
        parameter (arcpy.Parameter): Parameter to check.

    Returns:
        Current parameter value.
    """
    if hasattr(parameter, "values"):
        if parameter.values is None:
            value = None
        elif parameter.datatype == "Value Table":
            value = []
            for row in parameter.values:
                subval = tuple(
                    val
                    if type(val).__name__ != "geoprocessing value object"
                    else parameter_value(val)
                    for val in row
                )
                value.append(subval)
            value = tuple(value)
        else:
            value = tuple(
                val
                if type(val).__name__ != "geoprocessing value object"
                else parameter_value(val)
                for val in parameter.values
            )
    else:
        if parameter.value is None:
            value = None
        elif type(parameter.value).__name__ == "geoprocessing value object":
            value = parameter_value(parameter.value)
        else:
            value = parameter.value
    return value


def parameter_value_map(parameters):
    """Create value map from parameter.

    Args:
        parameters (list of arcpy.Parameter): Tool parameters.

    Returns:
        dict: {parameter-name: parameter-value}
    """
    return {parameter.name: parameter_value(parameter) for parameter in parameters}
