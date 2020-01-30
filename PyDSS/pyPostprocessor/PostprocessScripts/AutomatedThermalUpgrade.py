from PyDSS.pyPostprocessor.pyPostprocessAbstract import AbstractPostprocess
# Additional packages
import os
import matplotlib.pyplot as plt
import csv
import pandas as pd
import math
import opendssdirect as dss
import networkx as nx
import time
import json
# from place_new_regs import place_new_regs
import numpy as np
import seaborn as sns
import scipy.spatial.distance as ssd
from sklearn.cluster import AgglomerativeClustering
import matplotlib.image as mpimg
from PyDSS.pyPostprocessor.PostprocessScripts.postprocess_thermal_upgrades import postprocess_thermal_upgrades
plt.rcParams.update({'font.size': 14})

# For an overloaded line if a sensible close enough line code is available then simply change the line code
#  else add a new line in parallel
# Does not correct switch thermal violations if any - will only work on line objects which are not marked as switches
# In this part of the code since lines and DTs
# The available upgrades options can be read from an external library as well, currently being created by reading
#  through the DTs and lines available in the feeder itself.
# TODO: Add xhl, xht, xlt and buses functionality for DTs
# TODO: Units of the line and transformers

class AutomatedThermalUpgrade(AbstractPostprocess):
    """The class is used to induce faults on bus for dynamic simulation studies. Subclass of the :class:`PyDSS.pyControllers.pyControllerAbstract.ControllerAbstract` abstract class. 

    :param FaultObj: A :class:`PyDSS.dssElement.dssElement` object that wraps around an OpenDSS 'Fault' element
    :type FaultObj: class:`PyDSS.dssElement.dssElement`
    :param Settings: A dictionary that defines the settings for the faul controller.
    :type Settings: dict
    :param dssInstance: An :class:`opendssdirect` instance
    :type dssInstance: :class:`opendssdirect` instance
    :param ElmObjectList: Dictionary of all dssElement, dssBus and dssCircuit ojects
    :type ElmObjectList: dict
    :param dssSolver: An instance of one of the classes defined in :mod:`PyDSS.SolveMode`.
    :type dssSolver: :mod:`PyDSS.SolveMode`
    :raises: AssertionError  if 'FaultObj' is not a wrapped OpenDSS Fault element

    """
    def __init__(self, dssInstance, dssSolver, dssObjects, dssObjectsByClass, simulationSettings, Logger):
        """Constructor method
        """
        super(AutomatedThermalUpgrade).__init__()
        self.Settings = simulationSettings
        # New settings to be added into simulationsettings

        New_settings = {
            "Feeder": "../Test_Feeder_RIN_69_08_2030_sb100",  # "../Test_Feeder_J1",
            "img_path": "../Images",
            "DPV_scenarios": "../ten_random_RIN_69_08_2030_sb100",  # "../ten_random_J1",
            "master file": "MasterDisco.dss",  # "Master.dss",
            "DPV_penetration_HClimit": 0,
            "DPV_penetration_target": 0,
            "DPV_penetration_step": 10,
            "DPV control": "PF=1",  # "PF=1" or "PF=-0.95" or "VVar-CatA" or "VVar-CatB" or "VVar-VWatt-CatB"
            "DPV system priority": "watt",  # "watt" or "var"
            "Outputs": r"C:\Documents_NREL\Grid_Cost_DER_PhaseII\Control_device_placement\Outputs",
            "line loading limit": 1.0,  # 1=100%
            "DT loading limit": 1.0,  # 1=100%,
            "line_safety_margin": 1.5,
            # 1.1=110%: implies new line added should be rated at least 10% above the DTs actual overloading
            "xfmr_safety_margin": 1.5,
            # 1.1=110%: implies new DT added should be rated at least 10% above DT's actual overloading
            "V_upper_lim": 1.05,
            "V_lower_lim": 0.95,
            "Target_V": 1,
            "plot window open time": 1,  # seconds
            "Min PVLoad multiplier": 0.5,
            "Min Load multiplier": 0.1,
            "Max Load multiplier": 1,
            "max Regulators": 5,
            "Range B upper": 1.1,  # Range B limit inc
            "Range B lower": 0.20,
            "nominal_voltage": 120,
            "Max iterations": 20,
            "Create_upgrade_plots": True,
            "tps_to_test": [0.2, 1.8, 0.1, 0.9],
            # [min load multiplier without PV, max load multiplier without PV, min load multiplier with PV, max load multiplier with PV]
            "units key": ["mi", "kft", "km", "m", "Ft", "in", "cm"],  # Units key for lines taken from OpenDSS
            "max control iterations": 50,
            "Create_upgrades_library": False,
            }
        for key,val in New_settings.items():
            if key not in self.Settings:
                self.Settings[key] = val
        # self.__dssinstance = dssInstance
        self.logger = Logger
        dss = dssInstance
        self.dssSolver = dssSolver
        # TODO: To be modified
        start_pen = self.Settings["DPV_penetration_HClimit"]
        target_pen = self.Settings["DPV_penetration_target"]
        pen_step = self.Settings["DPV_penetration_step"]
        self.pen_level = start_pen
        #self.compile_feeder_initialize()
        self.export_line_DT_parameters()
        start_t = time.time()
        print("Determining thermal upgrades for PV penetration level: ", self.pen_level)

        self.feeder_parameters = {}

        self.dss_upgrades = [
            "//This file has all the upgrades determined using the control device placement algorithm \n"]

        # Determine available upgrades - either by looping over the existing feeder or by reading an
        #  externally created library
        if self.Settings["Create_upgrades_library"]:
            print("Creating upgrades library from the feeder")
            self.determine_available_line_upgrades()
            self.determine_available_xfmr_upgrades()
            print("Library created")
        else:
            print("Reading external upgrades library")
            self.avail_line_upgrades = self.read_available_upgrades("Line_upgrades_library")
            self.avail_xfmr_upgrades = self.read_available_upgrades("Transformer_upgrades_library")
            print("Upgrades library created")
        # Determine initial loadings
        self.determine_line_ldgs()
        self.determine_xfmr_ldgs()

        self.feeder_parameters["Initial violations"] = {
            "Number of lines with violations": len(self.line_violations),
            "Number of xfmrs with violations": len(self.xfmr_violations),
            "Max line loading observed": max(self.orig_line_ldg_lst),
            "Max xfmr loading observed": max(self.orig_xfmr_ldg_lst),
        }

        print("Voltages before upgrades", max(dss.Circuit.AllBusMagPu()), min(dss.Circuit.AllBusMagPu()))
        print("Substation power before upgrades: ", dss.Circuit.TotalPower())
        num_elms_violated_curr_itr = len(self.xfmr_violations) + len(self.line_violations)
        print(num_elms_violated_curr_itr)
        # Store all initial violations to compare at the end of solution
        self.temp_line_viols = self.all_line_ldgs
        self.temp_xfmr_viols = self.all_xfmr_ldgs
        self.orig_line_ldg_lst = []
        self.orig_xfmr_ldg_lst = []
        self.equip_ldgs = {}
        self.equip_ldgs["Equipment_name"] = "Initial_loading"
        for key, vals in self.temp_line_viols.items():
            self.orig_line_ldg_lst.append(vals[0] * 100 / vals[1])
            self.equip_ldgs["Line_" + key] = (vals[0] * 100 / vals[1])
        for key, vals in self.temp_xfmr_viols.items():
            self.orig_xfmr_ldg_lst.append(vals[0] * 100 / vals[1])
            self.equip_ldgs["Xfmr_" + key] = (vals[0] * 100 / vals[1])
        self.write_to_json(self.equip_ldgs, "Initial_equipment_loadings_pen_{}".format(self.pen_level))

        if self.Settings["Create_upgrade_plots"]:
            self.create_op_plots()

        # Mitigate thermal violations
        self.Line_trial_counter = 0
        while len(self.xfmr_violations) > 0 or len(self.line_violations) > 0:
            self.dssSolver.Solve()
            self.determine_line_ldgs()
            self.correct_line_violations()
            self.determine_xfmr_ldgs()
            self.correct_xfmr_violations()
            num_elms_violated_curr_itr = len(self.xfmr_violations) + len(self.line_violations)
            print("Iteration Count: ", self.Line_trial_counter)
            print("Number of devices with violations: ", num_elms_violated_curr_itr)
            self.Line_trial_counter += 1
            if self.Line_trial_counter > self.Settings["Max iterations"]:
                print("Max iterations limit reached, quitting")
                break
        self.determine_xfmr_ldgs()
        self.determine_line_ldgs()
        end = time.time()

        # Get final loadings
        self.final_line_ldg_lst = []
        self.final_xfmr_ldg_lst = []
        self.equip_ldgs = {}
        self.equip_ldgs["Equipment_name"] = "Final_loading"
        for key, vals in self.all_line_ldgs.items():
            self.final_line_ldg_lst.append(vals[0] * 100 / vals[1])
            self.equip_ldgs["Line_" + key] = (vals[0] * 100 / vals[1])
        for key, vals in self.all_xfmr_ldgs.items():
            self.final_xfmr_ldg_lst.append(vals[0] * 100 / vals[1])
            self.equip_ldgs["Xfmr_" + key] = (vals[0] * 100 / vals[1])
        self.write_to_json(self.equip_ldgs, "Final_equipment_loadings_pen_{}".format(self.pen_level))
        self.orig_line_ldg_lst.sort(reverse=True)
        self.final_line_ldg_lst.sort(reverse=True)
        self.orig_xfmr_ldg_lst.sort(reverse=True)
        self.final_xfmr_ldg_lst.sort(reverse=True)
        if self.Settings["Create_upgrade_plots"]:
            self.create_op_plots()

        # Store final results
        print("Simulation Time:", end - start)
        print("After upgrades the solution converged: ", dss.Solution.Converged())
        print("Voltages after upgrades ", max(dss.Circuit.AllBusMagPu()), min(dss.Circuit.AllBusMagPu()))
        print("Substation power after upgrades: ", dss.Circuit.TotalPower())
        if self.Settings["Create_upgrade_plots"]:
            plt.clf()
            plt.plot(self.orig_line_ldg_lst, "o", label="Starting Line Loadings")
            plt.plot(self.final_line_ldg_lst, "o", label="Ending Line Loadings")
            plt.plot(self.orig_xfmr_ldg_lst, "o", label="Starting Transformer Loadings")
            plt.plot(self.final_xfmr_ldg_lst, "o", label="Ending Transformer Loadings")
            plt.legend()
            plt.savefig(
                os.path.join(self.Settings["Outputs"], "Loading_comparisons_pen_{}".format(str(self.pen_level))),
                dpi=300)
        print("Writing Results to output file")
        self.write_dat_file()
        print("Processing upgrade results")
        print("Total time = ", end - start_t)
        # # TODO: Test impact of applied settings - can't compile the feeder again in PyDSS
        # self.compile_feeder_initialize()
        # upgrades_file = os.path.join(self.Settings["Outputs"], "Thermal_upgrades_pen_{}.dss".format(self.pen_level))
        # dss.run_command("Redirect {}".format(upgrades_file))
        # self.dssSolver.Solve()
        # self.determine_line_ldgs()
        # self.determine_xfmr_ldgs()
        # if self.Settings["Create_upgrade_plots"]:
        #     self.create_op_plots()

        self.feeder_parameters["Final violations"] = {
            "Number of lines with violations": len(self.line_violations),
            "Number of xfmrs with violations": len(self.xfmr_violations),
            "Max line loading observed": max(self.final_line_ldg_lst),
            "Max xfmr loading observed": max(self.final_xfmr_ldg_lst),
        }
        self.feeder_parameters["Simulation time (seconds)"] = end - start_t
        self.write_to_json(self.feeder_parameters, "Thermal_violations_comparison")

        # Process outputs
        input_dict = {
            "Feeder": self.Settings["Feeder"],
            "master file": self.Settings["master file"],
            "DPV_penetration_HClimit": self.Settings["DPV_penetration_HClimit"],
            "DPV_penetration_target": self.Settings["DPV_penetration_target"],
            "DPV_penetration_step": self.Settings["DPV_penetration_step"],
            "Outputs": self.Settings["Outputs"],
            "Create_plots": self.Settings["Create_upgrade_plots"]
        }

        postprocess_thermal_upgrades(input_dict, dss)

    def read_available_upgrades(self, file):
        f = open(os.path.join(self.Settings["Outputs"], "{}.json".format(file)), 'r')
        data = json.load(f)
        return data

    def create_op_plots(self):
        self.all_bus_names = dss.Circuit.AllBusNames()
        self.G = nx.DiGraph()
        self.generate_nodes()
        self.generate_edges()
        self.pos_dict = nx.get_node_attributes(self.G, 'pos')
        if self.Settings["Create_upgrade_plots"]:
            self.correct_node_coords()
        print("Length:", len(self.pos_dict))
        self.create_edge_node_dicts()
        self.plot_feeder()

    def correct_node_coords(self):
        # If node doesn't have node attributes, attach parent or child node's attributes
        new_temp_graph = self.G
        temp_graph = new_temp_graph.to_undirected()
        dss.Vsources.First()
        self.source = dss.CktElement.BusNames()[0].split(".")[0]
        for key, vals in self.pos_dict.items():
            if vals[0] == 0.0 and vals[1] == 0.0:
                new_x = 0
                new_y = 0
                pred_buses = nx.shortest_path(temp_graph, source=key, target=self.source)
                if len(pred_buses) > 0:
                    for pred_bus in pred_buses:
                        if pred_bus == key:
                            continue
                        if self.pos_dict[pred_bus][0] != 0.0 and self.pos_dict[pred_bus][1] != 0.0:
                            new_x = self.pos_dict[pred_bus][0]
                            new_y = self.pos_dict[pred_bus][1]
                            self.G.node[key]["pos"] = [new_x, new_y]
                            break
                if new_x == 0 and new_y == 0:
                    # Since either predecessor nodes were not available or they did not have
                    # non-zero coordinates, try successor nodes
                    # Get a leaf node
                    for x in self.G.nodes():
                        if self.G.out_degree(x) == 0 and self.G.in_degree(x) == 1:
                            leaf_node = x
                            break
                    succ_buses = nx.shortest_path(temp_graph, source=key, target=leaf_node)
                    if len(succ_buses) > 0:
                        for pred_bus in succ_buses:
                            if pred_bus == key:
                                continue
                            if self.pos_dict[pred_bus][0] != 0.0 and self.pos_dict[pred_bus][1] != 0.0:
                                new_x = self.pos_dict[pred_bus][0]
                                new_y = self.pos_dict[pred_bus][1]
                                self.G.node[key]["pos"] = [new_x, new_y]
                                break
        # Update pos dict with new coordinates
        self.pos_dict = nx.get_node_attributes(self.G, 'pos')

    def generate_nodes(self):
        self.nodes_list = []
        for b in self.all_bus_names:
            dss.Circuit.SetActiveBus(b)
            name = b.lower()
            position = []
            position.append(dss.Bus.X())
            position.append(dss.Bus.Y())
            self.G.add_node(name, pos=position)
            self.nodes_list.append(b)

    def generate_edges(self):
        '''
        All lines, switches, reclosers etc are modeled as lines, so calling lines takes care of all of them.
        However we also need to loop over transformers as they form the edge between primary and secondary nodes
        :return:
        '''
        dss.Lines.First()
        while True:
            from_bus = dss.Lines.Bus1().split('.')[0].lower()
            to_bus = dss.Lines.Bus2().split('.')[0].lower()
            phases = dss.Lines.Phases()
            length = dss.Lines.Length()
            name = dss.Lines.Name()
            self.G.add_edge(from_bus, to_bus, phases=phases, length=length, name=name)
            if not dss.Lines.Next() > 0:
                break

        dss.Transformers.First()
        while True:
            bus_names = dss.CktElement.BusNames()
            from_bus = bus_names[0].split('.')[0].lower()
            to_bus = bus_names[1].split('.')[0].lower()
            phases = dss.CktElement.NumPhases()
            length = 0.0
            name = dss.Transformers.Name()
            self.G.add_edge(from_bus, to_bus, phases=phases, length=length, name=name)
            if not dss.Transformers.Next() > 0:
                break

    def create_edge_node_dicts(self):
        self.edge_to_plt_dict = []
        self.edge_pos_plt_dict = {}
        self.edge_size_list = []
        self.DT_sec_lst = []
        self.DT_size_list = []
        self.DT_sec_coords = {}
        for key,vals in self.equip_ldgs.items():
            if key.lower().startswith("line") and int(vals)>100:
                key = key.split("Line_")[1]
                key = "Line."+key
                dss.Circuit.SetActiveElement("{}".format(key))
                from_bus = dss.CktElement.BusNames()[0].split(".")[0]
                to_bus = dss.CktElement.BusNames()[1].split(".")[0]
                self.edge_to_plt_dict.append((from_bus, to_bus))
                self.edge_pos_plt_dict[from_bus] = self.pos_dict[from_bus]
                self.edge_pos_plt_dict[to_bus] = self.pos_dict[to_bus]
                self.edge_size_list.append(vals)
            if key.lower().startswith("xfmr") and int(vals)>100:
                key = key.split("Xfmr_")[1]
                key = "Transformer." + key
                dss.Circuit.SetActiveElement("{}".format(key))
                bus_sec = dss.CktElement.BusNames()[1].split(".")[0]
                self.DT_sec_lst.append(bus_sec)
                self.DT_size_list.append(vals*30)
                self.DT_sec_coords[bus_sec] = self.pos_dict[bus_sec]

    def plot_feeder(self):
        plt.figure(figsize=(40, 40), dpi=10)
        if len(self.edge_size_list)>0:
            de = nx.draw_networkx_edges(self.G, pos=self.edge_pos_plt_dict, edgelist=self.edge_to_plt_dict, edge_color="r",
                                        alpha=0.5, width=self.edge_size_list)
        ec = nx.draw_networkx_edges(self.G, pos=self.pos_dict, alpha=1.0, width=1)
        if len(self.DT_sec_lst)>0:
            dt = nx.draw_networkx_nodes(self.G, pos=self.DT_sec_coords, nodelist=self.DT_sec_lst, node_size=self.DT_size_list,
                                        node_color='deeppink', alpha=1)
        ldn = nx.draw_networkx_nodes(self.G, pos=self.pos_dict, nodelist=self.nodes_list, node_size=1,
                                     node_color='k', alpha=1)
        # nx.draw_networkx_labels(self.G, pos=self.pos_dict, node_size=1, font_size=15)
        plt.title("Thermal violations")
        plt.axis("off")
        plt.show()
        #plt.savefig(os.path.join(self.Settings["Outputs"],"Thermal_upgrades_in_pen_{}.pdf".format(self.pen_level)))

    def export_line_DT_parameters(self):
        self.originial_line_parameters = {}
        self.originial_xfmr_parameters = {}
        dss.Lines.First()
        while True:
            ln_name = dss.Lines.Name()
            ln_lc = dss.Lines.LineCode()
            ln_len = dss.Lines.Length()
            len_units = self.Settings["units key"][dss.Lines.Units()-1]
            #len_units_alt = dss.Properties.Value("Units")
            ln_phases = dss.Lines.Phases()
            ln_b1 = dss.Lines.Bus1()
            ln_b2 = dss.Lines.Bus2()
            dss.Circuit.SetActiveBus(ln_b1)
            kv_b1 = dss.Bus.kVBase()
            dss.Circuit.SetActiveBus(ln_b2)
            kv_b2 = dss.Bus.kVBase()
            dss.Circuit.SetActiveElement("Line.{}".format(ln_name))
            if kv_b1!=kv_b2:
                print("To and from bus voltages ({} {}) do not match for line {}, quitting....".format(kv_b2, kv_b1, ln_name))
                quit()
            self.originial_line_parameters[ln_name] = {"linecode":ln_lc,"length": ln_len,
                                                       "num_phases": ln_phases,"line_kV": kv_b1,
                                                       "length_unit":len_units}
            if not dss.Lines.Next()>0:
                break
        self.write_to_json(self.originial_line_parameters, "Original_line_parameters")

        self.Linecodes_params = {}
        dss.LineCodes.First()
        while True:
            lc_name = dss.LineCodes.Name()
            ampacity = dss.LineCodes.NormAmps()
            self.Linecodes_params[lc_name] = {"Ampacity":ampacity}
            if not dss.LineCodes.Next()>0:
                break
        self.write_to_json(self.Linecodes_params, "Original_linecodes_parameters")

        dss.Transformers.First()
        while True:
            dt_name = dss.Transformers.Name()
            dt_phases = dss.Properties.Value("Phases")
            dt_numwdgs = dss.Transformers.NumWindings()
            dt_kva = []
            dt_conn = []
            dt_kv = []
            for wdgs in range(dt_numwdgs):
                dss.Transformers.Wdg(wdgs+1)
                dt_kva.append(float(dss.Properties.Value("kva")))
                dt_kv.append(float(dss.Properties.Value("kv")))
                dt_conn.append(dss.Properties.Value("conn"))
            self.originial_xfmr_parameters[dt_name] = {"num_phases":dt_phases,"num_windings": dt_numwdgs,
                                                       "wdg_kvas": dt_kva,"wdg_kvs": dt_kv,"wdg_conns": dt_conn}
            if not dss.Transformers.Next()>0:
                break
        self.write_to_json(self.originial_xfmr_parameters, "Original_xfmr_parameters")

    def process_DT_upgrades_file(self):
        self.compiled_xfmr_upgrades = {}
        f = open(os.path.join(self.Settings["Outputs"],"Originial_xfmr_parameters.json"), 'r')
        data = json.load(f)
        for xfmr,specs in data.items():
            par_xfmr_cnt = 0
            with open(os.path.join(self.Settings["Outputs"],"Thermal_upgrades.dss"), "r") as datafile:
                for line in datafile:
                    if line.startswith("New Transformer."):
                        params = line.split()
                        for param in params:
                            if param.startswith("Transformer."):
                                dt_name = param.split(".")[0]
                    elif line.startswith("Edit Transformer."):
                        print("")
        return

    def write_to_json(self, dict, file_name):
        with open(os.path.join(self.Settings["Outputs"],"{}.json".format(file_name)), "w") as fp:
            json.dump(dict, fp, indent=4)


    def write_dat_file(self):
        with open(os.path.join(self.Settings["Outputs"],"Thermal_upgrades_pen_{}.dss".format(str(self.pen_level))), "w") as datafile:
            for line in self.dss_upgrades:
                datafile.write(line)

    def determine_available_line_upgrades(self):
        self.avail_line_upgrades = {}
        dss.Lines.First()
        while True:
            line_name = dss.Lines.Name()
            line_code = dss.Lines.LineCode()
            ln_geo = ''
            ln_config = "linecode"
            if line_code=='':
                ln_geo      = dss.Lines.Geometry()
                ln_config = "geometry"
                # TODO: Distinguish between overhead and underground cables, currently there is no way to distinguish using opendssdirect/pydss etc
                # dss.Circuit.SetActiveClass("linegeometry")
                # flag = dss.ActiveClass.First()
                # while flag>0:
                #     print(dss.ActiveClass.Name(),dss.Properties.Value("wire"),dss.Properties.Value("cncable"),dss.Properties.Value("tscable"))
                #     flag = dss.ActiveClass.Next()
            phases = dss.Lines.Phases()
            norm_amps = dss.CktElement.NormalAmps()
            # TODO change this to properties
            from_bus = dss.CktElement.BusNames()[0].split(".")[0]
            to_bus = dss.CktElement.BusNames()[1].split(".")[0]
            dss.Circuit.SetActiveBus(from_bus)
            kv_from_bus = dss.Bus.kVBase()
            dss.Circuit.SetActiveBus(to_bus)
            kv_to_bus = dss.Bus.kVBase()
            dss.Circuit.SetActiveElement("Line.{}".format(line_name))
            if kv_from_bus!=kv_to_bus:
                print("For line {} the from and to bus kV ({} {}) do not match, quitting...".format(line_name,
                                                                                                    kv_from_bus,
                                                                                                    kv_to_bus))
                quit()
            key = "type_"+"{}_".format(ln_config)+"{}_".format(phases)+"{}".format(round(kv_from_bus,3))
            # Add linecodes
            if key not in self.avail_line_upgrades and ln_config=="linecode":
                self.avail_line_upgrades[key] = {"{}".format(line_code):[norm_amps]}
            elif key in self.avail_line_upgrades and ln_config=="linecode":
                for lc_key,lc_dict in self.avail_line_upgrades.items():
                    if line_code not in lc_dict:
                        self.avail_line_upgrades[key]["{}".format(line_code)]=[norm_amps]
            # Add line geometries
            if key not in self.avail_line_upgrades and ln_config == "geometry":
                self.avail_line_upgrades[key] = {"{}".format(ln_geo): [norm_amps]}
            elif key in self.avail_line_upgrades and ln_config == "geometry":
                for lc_key, lc_dict in self.avail_line_upgrades.items():
                    if ln_geo not in lc_dict:
                        self.avail_line_upgrades[key]["{}".format(ln_geo)] = [norm_amps]
            if not dss.Lines.Next()>0:
                break

    def determine_line_ldgs(self):
        self.line_violations = {}
        self.all_line_ldgs = {}
        self.solve_diff_tps_lines()
        for key,vals in self.line_violations_alltps.items():
            self.line_violations[key] = [max(vals[0]),vals[1]]
        for key, vals in self.all_line_ldgs_alltps.items():
            self.all_line_ldgs[key] = [max(vals[0]), vals[1]]

    def solve_diff_tps_lines(self):
        # Uses Kwami's LA100 logic
        print("PVsystems:",dss.PVsystems.Count())
        self.line_violations_alltps = {}
        self.all_line_ldgs_alltps = {}
        for tp_cnt in range(len(self.Settings["tps_to_test"])):
            # First two tps are for disabled PV case
            if tp_cnt==0 or tp_cnt==1:
                dss.run_command("BatchEdit PVSystem..* Enabled=False")
                dss.run_command("set LoadMult = {LM}".format(LM = self.Settings["tps_to_test"][tp_cnt]))
                self.dssSolver.Solve()
                if not dss.Solution.Converged():
                    print("OpenDSS solution did not converge, quitting...")
                    quit()
                print(dss.Circuit.TotalPower())
                print("tp count", tp_cnt)
            if tp_cnt==2 or tp_cnt==3:
                dss.run_command("BatchEdit PVSystem..* Enabled=True")
                dss.run_command("set LoadMult = {LM}".format(LM = self.Settings["tps_to_test"][tp_cnt]))
                self.dssSolver.Solve()
                print(dss.Circuit.TotalPower())
                if not dss.Solution.Converged():
                    print("OpenDSS solution did not converge, quitting...")
                    quit()
                print("tp count", tp_cnt)
            dss.Circuit.SetActiveClass("Line")
            dss.ActiveClass.First()
            while True:
                switch = dss.Properties.Value("switch")
                line_name = dss.CktElement.Name().split(".")[1].lower()
                n_phases = dss.CktElement.NumPhases()
                line_limit = dss.CktElement.NormalAmps()
                Currents = dss.CktElement.CurrentsMagAng()[:2 * n_phases]
                line_current = Currents[::2]
                ldg = round( max(line_current)/ float(line_limit), 2)
                if switch == "False":
                    if line_name not in self.all_line_ldgs_alltps:
                        self.all_line_ldgs_alltps[line_name] = [[max(line_current)], line_limit]
                    elif line_name in self.all_line_ldgs_alltps:
                        self.all_line_ldgs_alltps[line_name][0].append(max(line_current))
                if ldg > self.Settings["line loading limit"] and switch == "False":  # and switch==False:
                    if line_name not in self.line_violations_alltps:
                        self.line_violations_alltps[line_name] = [[max(line_current)], line_limit]
                    elif line_name in self.line_violations_alltps:
                        self.line_violations_alltps[line_name][0].append(max(line_current))
                if not dss.ActiveClass.Next() > 0:
                    break
        return

    def correct_line_violations(self):
        # This finds a line code which provides a specified safety margin to a line above its maximum observed loading.
        # If a line code is not found or if line code is too overrated one or more parallel lines (num_par_lns-1)
        # may be added.

        if len(self.line_violations)>0:
            for key,vals in self.line_violations.items():
                dss.Lines.Name("{}".format(key))
                phases = dss.Lines.Phases()
                length = dss.Lines.Length()
                units = self.Settings["units key"][dss.Lines.Units()-1]
                ln_geo = ''
                line_code = dss.Lines.LineCode()
                ln_config = "linecode"
                if line_code=='':
                    line_code = dss.Lines.Geometry()
                    ln_config = "geometry"
                from_bus = dss.CktElement.BusNames()[0]
                to_bus = dss.CktElement.BusNames()[1]
                dss.Circuit.SetActiveBus(from_bus)
                kv_from_bus = dss.Bus.kVBase()
                dss.Circuit.SetActiveBus(to_bus)
                kv_to_bus = dss.Bus.kVBase()
                dss.Circuit.SetActiveElement("Line.{}".format(key))
                norm_amps = dss.CktElement.NormalAmps()
                num_par_lns = int(vals[0]*self.Settings["line_safety_margin"]/vals[1])+1
                if kv_from_bus != kv_to_bus:
                    print("For line {} the from and to bus kV ({} {}) do not match, quitting...".format(key,
                                                                                                        kv_from_bus,
                                                                                                        kv_to_bus))
                    quit()
                if float(norm_amps)-float(vals[1])>0.001:
                    print("For line {} the rated current values ({} {}) do not match, quitting...".format(key,
                                                                                                          norm_amps,
                                                                                                          vals[1]))
                    quit()
                lc_key = "type_"+"{}_".format(ln_config)+"{}_".format(phases)+"{}".format(round(kv_from_bus,3))
                lcs_fnd_flag = 0
                if lc_key in self.avail_line_upgrades:
                    for lcs,lcs_vals in self.avail_line_upgrades[lc_key].items():
                        if lcs!=line_code:
                            if lcs_vals[0]>vals[0]*self.Settings["line_safety_margin"] and lcs_vals[0]<num_par_lns*norm_amps:
                                command_string = "Edit Line.{lnm} {cnfig}={lnc}".format(lnm=key, cnfig=ln_config, lnc=lcs)
                                dss.run_command(command_string)
                                self.dssSolver.Solve()
                                self.write_dss_file(command_string)
                                lcs_fnd_flag = 1
                                break
                if lc_key not in self.avail_line_upgrades or lcs_fnd_flag==0:
                    # Add parallel lines since no suitable (correct ampacity/ratings or economical) line
                    # replacement was found
                    # print("{} Parallel lines required for line {}".format(num_par_lns-1, key))
                    for ln_cnt in range(num_par_lns-1):
                        command_string = "New Line.{lnm}_upgrade_{tr_cnt}_{cnt} bus1={b1} bus2={b2} length={lt} units={u}" \
                                         " {cnfig}={lc} phases={ph} enabled=True".format(
                            lnm=key,
                            tr_cnt = self.Line_trial_counter,
                            cnt=ln_cnt,
                            b1=from_bus,
                            b2=to_bus,
                            lt=length,
                            u=units,
                            cnfig = ln_config,
                            lc=line_code,
                            ph=phases
                        )
                        dss.run_command(command_string)
                        self.dssSolver.Solve()
                        self.write_dss_file(command_string)
        else:
            print("This DPV penetration has no line violations")
        return

    def determine_available_xfmr_upgrades(self):
        # If the kVA ratings for the windings do not match, the DT is not considered as a potential upgrade option.
        #  For this DT the upgrade will either be one or more DTs of its own ratings in parallel or a new DT with
        #  higher ratings but each winding will be of equal rating.
        # Get a unique id descriptive of DT number of phases, number of wdgs; wdg kvs and connections
        # Get essential DT characteristics: kVA, normal amps rating, %r for both windings to capture percent load loss,
        # and % noload loss
        self.avail_xfmr_upgrades = {}
        dss.Transformers.First()
        while True:
            ignore_upgrade=0
            xfmr_name = dss.Transformers.Name()
            # Figure out unique DT characteristics so possible upgrade options may be determined: key is: num phases;
            # num wdgs; kv and connection of each winding
            phases = dss.CktElement.NumPhases()
            num_wdgs = dss.Transformers.NumWindings()
            norm_amps = dss.CktElement.NormalAmps()
            conn_list = []
            wdg_kva_list = []
            wdg_kv_list = []
            per_R_list = []
            per_losses = []
            per_reac = []
            per_reac.append(dss.Properties.Value("xhl"))
            if num_wdgs==3:
                per_reac.append(dss.Properties.Value("xht"))
                per_reac.append(dss.Properties.Value("xlt"))
            for wdgs in range(num_wdgs):
                dss.Transformers.Wdg(wdgs+1)
                wdg_kva_list.append(float(dss.Properties.Value("kva")))
                wdg_kv_list.append(float(dss.Properties.Value("kv")))
                conn_list.append(dss.Properties.Value("conn"))
                per_R_list.append(dss.Properties.Value("%r"))
            per_losses.append(dss.Properties.Value("%noloadloss"))
            per_losses.append(dss.Properties.Value("%loadloss"))
            if per_losses[0]>per_losses[1]:
                print("For DT {}, %noloadloss is greater than %loadloss {}, continuing...".format(xfmr_name,
                                                                                                          per_losses))
            for i in wdg_kva_list:
                if i!=wdg_kva_list[0]:
                    print(" DT {} will not be considered as a upgrade option as the kVA values of its" \
                          " windings do not match {}".format(xfmr_name,wdg_kva_list))
                    ignore_upgrade = 1
                    break
            if ignore_upgrade==1:
                continue
            key = "type_"+"{}_".format(phases)+"{}_".format(num_wdgs)
            for kv_cnt in range(len(wdg_kv_list)):
                key = key + "{}_".format(wdg_kv_list[kv_cnt])
                key = key + "{}_".format(conn_list[kv_cnt])
            # Get relevant parameters for each potential upgrade option
            if key not in self.avail_xfmr_upgrades:
                self.avail_xfmr_upgrades[key] = {xfmr_name:[wdg_kva_list,[norm_amps],per_R_list,per_losses,per_reac]}
            elif key in self.avail_xfmr_upgrades:
                if xfmr_name not in self.avail_xfmr_upgrades[key]:
                    self.avail_xfmr_upgrades[key][xfmr_name] = [wdg_kva_list,[norm_amps],per_R_list,per_losses,per_reac]
            if not dss.Transformers.Next()>0:
                break
        return

    def determine_xfmr_ldgs(self):
        # TODO: LA100 team please correct the logic used for determining DT loadings. This is the same logic Nicolas
        # TODO: used for the SETO project. This may break down for some DT configurations such as 3 wdg transformers
        self.xfmr_violations = {}
        self.all_xfmr_ldgs = {}
        self.determine_xfmr_ldgs_alltps()
        for key,vals in self.xfmr_violations_alltps.items():
            self.xfmr_violations[key] = [max(vals[0]),vals[1]]
        for key,vals in self.all_xfmr_ldgs_alltps.items():
            self.all_xfmr_ldgs[key] = [max(vals[0]),vals[1]]

    def determine_xfmr_ldgs_alltps(self):
        self.xfmr_violations_alltps = {}
        self.all_xfmr_ldgs_alltps = {}
        for tp_cnt in range(len(self.Settings["tps_to_test"])):
            # First two tps are for disabled PV case
            if tp_cnt == 0 or tp_cnt == 1:
                dss.run_command("BatchEdit PVSystem..* Enabled=False")
                dss.run_command("set LoadMult = {LM}".format(LM=self.Settings["tps_to_test"][tp_cnt]))
                self.dssSolver.Solve()
                if not dss.Solution.Converged():
                    print("OpenDSS solution did not converge, quitting...")
                    quit()
                print("tp count", tp_cnt)
                print(dss.Circuit.TotalPower())
            if tp_cnt == 2 or tp_cnt == 3:
                dss.run_command("BatchEdit PVSystem..* Enabled=True")
                dss.run_command("set LoadMult = {LM}".format(LM=self.Settings["tps_to_test"][tp_cnt]))
                self.dssSolver.Solve()
                if not dss.Solution.Converged():
                    print("OpenDSS solution did not converge, quitting...")
                    quit()
                print("tp count", tp_cnt)
                print(dss.Circuit.TotalPower())
            dss.Transformers.First()
            while True:
                xfmr_name = dss.CktElement.Name().split(".")[1].lower()
                # Kwami's approach
                n_phases = dss.CktElement.NumPhases()
                hs_kv = float(dss.Properties.Value('kVs').split('[')[1].split(',')[0])
                kva = float(dss.Properties.Value('kVA'))
                if n_phases > 1:
                    xfmr_limit = kva / (hs_kv * math.sqrt(3))
                else:
                    xfmr_limit = kva / (hs_kv)
                Currents = dss.CktElement.CurrentsMagAng()[:2 * n_phases]
                xfmr_current = Currents[::2]
                max_flow = max(xfmr_current)
                ldg = max_flow / xfmr_limit
                if xfmr_name not in self.all_xfmr_ldgs_alltps:
                    self.all_xfmr_ldgs_alltps[xfmr_name] = [[max(xfmr_current)], xfmr_limit]
                elif xfmr_name in self.all_xfmr_ldgs_alltps:
                    self.all_xfmr_ldgs_alltps[xfmr_name][0].append(max(xfmr_current))
                if ldg > self.Settings["DT loading limit"]:
                    if xfmr_name not in self.xfmr_violations_alltps:
                        self.xfmr_violations_alltps[xfmr_name] = [[max(xfmr_current)], xfmr_limit]
                    elif xfmr_name in self.xfmr_violations_alltps:
                        self.xfmr_violations_alltps[xfmr_name][0].append(max(xfmr_current))
                if not dss.Transformers.Next() > 0:
                    break
        return

    def correct_xfmr_violations(self):
        # This finds a line code which provides a specified safety margin to a line above its maximum observed loading.
        # If a line code is not found or if line code is too overrated one or more parallel lines (num_par_lns-1)
        # may be added.

        if len(self.xfmr_violations)>0:
            for key,vals in self.xfmr_violations.items():
                # Determine which category this DT belongs to
                # dss.Circuit.SetActiveElement("Transformer.{}".format(key))
                dss.Transformers.Name(key)
                phases = dss.CktElement.NumPhases()
                num_wdgs = dss.Transformers.NumWindings()
                #norm_amps = dss.CktElement.NormalAmps()
                #Kwami's logic
                n_phases = dss.CktElement.NumPhases()
                hs_kv = float(dss.Properties.Value('kVs').split('[')[1].split(',')[0])
                kva = float(dss.Properties.Value('kVA'))
                if n_phases > 1:
                    norm_amps = kva / (hs_kv * math.sqrt(3))
                else:
                    norm_amps = kva / (hs_kv)
                conn_list = []
                wdg_kva_list = []
                wdg_kv_list = []
                per_R_list = []
                per_losses = []
                per_reac = []
                per_reac.append(dss.Properties.Value("xhl"))
                if num_wdgs == 3:
                    per_reac.append(dss.Properties.Value("xht"))
                    per_reac.append(dss.Properties.Value("xlt"))
                for wdgs in range(num_wdgs):
                    dss.Transformers.Wdg(wdgs + 1)
                    wdg_kva_list.append(float(dss.Properties.Value("kva")))
                    wdg_kv_list.append(float(dss.Properties.Value("kv")))
                    conn_list.append(dss.Properties.Value("conn"))
                    per_R_list.append(dss.Properties.Value("%r"))
                buses_list = dss.CktElement.BusNames()
                per_losses.append(dss.Properties.Value("%noloadloss"))
                # per_losses.append(dss.Properties.Value("%loadloss"))
                # if per_losses[0] > per_losses[1]:
                #     print("For DT {}, %noloadloss is greater than %loadloss {}, continuing...".format(key,
                #                                                                                       per_losses))
                if float(norm_amps)-float(vals[1])>0.001:
                    print("For DT {} the rated current values ({} {}) do not match, quitting...".format(key,
                                                                                                          norm_amps,
                                                                                                          vals[1]))
                    quit()
                num_par_dts = int(vals[0] * self.Settings["xfmr_safety_margin"] / vals[1]) + 1
                dt_key = "type_" + "{}_".format(phases) + "{}_".format(num_wdgs)
                for kv_cnt in range(len(wdg_kv_list)):
                    dt_key = dt_key + "{}_".format(wdg_kv_list[kv_cnt])
                    dt_key = dt_key + "{}_".format(conn_list[kv_cnt])
                # Find potential upgrades for this DT. This might be a new higher kVA rated DT in place of the original or
                #  one or more parallel DTs
                dt_fnd_flag = 0
                if dt_key in self.avail_xfmr_upgrades:
                    for dt,dt_vals in self.avail_xfmr_upgrades[dt_key].items():
                        if dt!=key:
                            if dt_vals[1][0]>vals[0]*self.Settings["xfmr_safety_margin"] and dt_vals[1][0]<num_par_dts*norm_amps:
                                command_string = "Edit Transformer.{xf} %noloadloss={nll} ".format(xf=key,
                                                                                                  nll=dt_vals[3][0])
                                for wdgs_cnt in range(num_wdgs):
                                    wdg_str = "wdg={wdg_cnt} kVA={wdg_kva} %r={wdg_r} ".format(wdg_cnt=str(wdgs_cnt+1),
                                                                                               wdg_kva = dt_vals[0][wdgs_cnt],
                                                                                               wdg_r = dt_vals[2][wdgs_cnt])
                                    command_string = command_string + wdg_str
                                if num_wdgs==3:
                                    per_reac_str = "XLT={xlt} XHT={xht} ".format(xlt=dt_vals[4][2],
                                                                                xht=dt_vals[4][1])
                                    command_string = command_string + per_reac_str
                                add_str = "XHL={xhl} ".format(xhl=dt_vals[4][0])
                                dss.run_command(command_string)
                                self.dssSolver.Solve()
                                self.write_dss_file(command_string)
                                dt_fnd_flag=1
                                break
                if dt_key not in self.avail_xfmr_upgrades or dt_fnd_flag==0:
                    # Add parallel DTs since no suitable (correct ratings or economical) DT
                    # replacement was found
                    for dt_cnt in range(num_par_dts-1):
                        command_string = "New Transformer.{dtn}_upgrade_{tr_cnt}_{cnt} phases={phs} windings={wdgs}" \
                                         " %noloadloss={nll} ".format(
                            dtn=key,
                            tr_cnt = self.Line_trial_counter,
                            cnt=dt_cnt,
                            phs=phases,
                            wdgs=num_wdgs,
                            nll=per_losses[0]
                        )

                        for wdgs_cnt in range(num_wdgs):
                            wdg_str = "wdg={numwdg} bus={bus_wdg} kv={wdgs_kv} kVA={wdgs_kva} %r={wdgs_r} ".format(
                                numwdg=str(wdgs_cnt+1),
                                bus_wdg=buses_list[wdgs_cnt],
                                wdgs_kv=wdg_kv_list[wdgs_cnt],
                                wdgs_kva = wdg_kva_list[wdgs_cnt],
                                wdgs_r=per_R_list[wdgs_cnt]
                            )
                            command_string=command_string + wdg_str
                        if num_wdgs == 3:
                            per_reac_str = "XLT={xlt} XHT={xht} ".format(xlt=per_reac[2],
                                                                         xht=per_reac[1])
                            command_string = command_string + per_reac_str
                        add_str = "XHL={xhl} enabled=True".format(xhl=per_reac[0])
                        command_string=command_string+add_str
                        dss.run_command(command_string)
                        self.dssSolver.Solve()
                        self.write_dss_file(command_string)
        else:
            print("This DPV penetration has no Transformer thermal violations")
        return

    def write_dss_file(self, device_command):
        self.dss_upgrades.append(device_command+"\n")
        return

    def redirect_dss_upgrades(self):
        # TODO: Probably not useful here
        # Get all available dss upgrades files
        self.thermal_upgrades_files = [f for f in os.listdir(self.Settings["Outputs"]) if f.startswith("Thermal_upgrades_pen")]
        if int(self.pen_level) > int(self.Settings["DPV_penetration_HClimit"]):
            prev_pen_level = self.pen_level - self.Settings["DPV_penetration_step"]
            expected_file_name = "Thermal_upgrades_pen_{}.dss".format(prev_pen_level)
            print(expected_file_name)
            if expected_file_name in self.thermal_upgrades_files:
                expected_file_path = os.path.join(self.Settings["Outputs"],expected_file_name)
                dss.run_command("Redirect {}".format(expected_file_path))
                # Also append all upgrades in the previous penetration level to the next level
                with open(os.path.join(self.Settings["Outputs"], "Thermal_upgrades_pen_{}.dss".format(prev_pen_level)),"r") as datafile:
                    for line in datafile:
                        self.dss_upgrades.append(line)
            elif expected_file_name not in self.thermal_upgrades_files:
                print("Previous upgrades file does not exist, some error is code execution, quitting")
                quit()

    def run(self, step, stepMax):
        """Induces and removes a fault as the simulation runs as per user defined settings. 
        """
        self.logger.info('Running thermal upgrade post process')


        #step-=1 # uncomment the line if the post process needs to rerun for the same point in time
        return step

