"""Live ULS/Rare/Quasi static Normal moments using the existing FEM.

Original workbook load-factor/section mapping; not stored output forces.
The four-vehicle live-load envelope and thermal factors remain separately
unvalidated and are deliberately not fabricated here.
"""
from __future__ import annotations
import math
from dataclasses import replace
from .frame_solver import FrameOptions, run_frame_analysis
from .load_combinations import CombinationInputs
from .earth_pressure import EarthPressureInputs, TF_TO_KN

# Source factor sets and beam/end/section lookup metadata ONLY. No source output numbers.
CASE_FACTOR_DEFINITIONS = {'1001': {'501': 1.35, '502': 1.35, '503': 1.75},
 '1002': {'501': 1.35, '502': 1.35, '503': 1.75, '504': 1.5},
 '1003': {'501': 1.35, '502': 1.35, '503': 1.75, '504': 1.5, '505': 1.35},
 '1004': {'501': 1.35, '502': 1.35, '503': 1.75, '504': 1.5, '505': 1.35, '506': 1.2},
 '1005': {'501': 1.35, '502': 1.35, '503': 1.75, '504': 1.5, '505': 1.35, '507': 1.2},
 '1006': {'501': 1.35, '502': 1.35, '503': 1.75, '504': 1.5, '505': 1.35, '506': 1.2, '507': 1.2},
 '1007': {'501': 1.35, '502': 1.35, '503': 1.75, '508': 0.9},
 '1008': {'501': 1.35, '502': 1.35, '503': 1.75, '504': 1.5, '508': 0.9},
 '1009': {'501': 1.35, '502': 1.35, '503': 1.75, '504': 1.5, '505': 1.35, '508': 0.9},
 '1010': {'501': 1.35, '502': 1.35, '503': 1.75, '504': 1.5, '505': 1.35, '506': 1.2, '508': 0.9},
 '1011': {'501': 1.35, '502': 1.35, '503': 1.75, '504': 1.5, '505': 1.35, '507': 1.2, '508': 0.9},
 '1012': {'501': 1.35, '502': 1.35, '503': 1.75, '504': 1.5, '505': 1.35, '506': 1.2, '507': 1.2, '508': 0.9},
 '1013': {'501': 1.35, '502': 1.35, '503': 1.75, '509': 0.9},
 '1014': {'501': 1.35, '502': 1.35, '503': 1.75, '504': 1.5, '509': 0.9},
 '1015': {'501': 1.35, '502': 1.35, '503': 1.75, '504': 1.5, '505': 1.35, '509': 0.9},
 '1016': {'501': 1.35, '502': 1.35, '503': 1.75, '504': 1.5, '505': 1.35, '506': 1.2, '509': 0.9},
 '1017': {'501': 1.35, '502': 1.35, '503': 1.75, '504': 1.5, '505': 1.35, '507': 1.2, '509': 0.9},
 '1018': {'501': 1.35, '502': 1.35, '503': 1.75, '504': 1.5, '505': 1.35, '506': 1.2, '507': 1.2, '509': 0.9},
 '1019': {'501': 1.35, '502': 1.35, '503': 1.75, '510': 0.9},
 '1020': {'501': 1.35, '502': 1.35, '503': 1.75, '504': 1.5, '510': 0.9},
 '1021': {'501': 1.35, '502': 1.35, '503': 1.75, '504': 1.5, '505': 1.35, '510': 0.9},
 '1022': {'501': 1.35, '502': 1.35, '503': 1.75, '504': 1.5, '505': 1.35, '506': 1.2, '510': 0.9},
 '1023': {'501': 1.35, '502': 1.35, '503': 1.75, '504': 1.5, '505': 1.35, '507': 1.2, '510': 0.9},
 '1024': {'501': 1.35, '502': 1.35, '503': 1.75, '504': 1.5, '505': 1.35, '506': 1.2, '507': 1.2, '510': 0.9},
 '1025': {'501': 1.35, '502': 1.35, '503': 1.75, '511': 0.9},
 '1026': {'501': 1.35, '502': 1.35, '503': 1.75, '504': 1.5, '511': 0.9},
 '1027': {'501': 1.35, '502': 1.35, '503': 1.75, '504': 1.5, '505': 1.35, '511': 0.9},
 '1028': {'501': 1.35, '502': 1.35, '503': 1.75, '504': 1.5, '505': 1.35, '506': 1.2, '511': 0.9},
 '1029': {'501': 1.35, '502': 1.35, '503': 1.75, '504': 1.5, '505': 1.35, '507': 1.2, '511': 0.9},
 '1030': {'501': 1.35, '502': 1.35, '503': 1.75, '504': 1.5, '505': 1.35, '506': 1.2, '507': 1.2, '511': 0.9},
 '1031': {'501': 1.0, '502': 1.0, '503': 1.2},
 '1032': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0},
 '1033': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0},
 '1034': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '506': 0.8},
 '1035': {'501': 1.2, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '507': 0.8},
 '1036': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '506': 0.8, '507': 0.8},
 '1037': {'501': 1.0, '502': 1.0, '503': 1.2, '508': 0.6},
 '1038': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '508': 0.6},
 '1039': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '508': 0.6},
 '1040': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '506': 0.8, '508': 0.6},
 '1041': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '507': 0.8, '508': 0.6},
 '1042': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '506': 0.8, '507': 0.8, '508': 0.6},
 '1043': {'501': 1.0, '502': 1.0, '503': 1.2, '509': 0.6},
 '1044': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '509': 0.6},
 '1045': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '509': 0.6},
 '1046': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '506': 0.8, '509': 0.6},
 '1047': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '507': 0.8, '509': 0.6},
 '1048': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '506': 0.8, '507': 0.8, '509': 0.6},
 '1049': {'501': 1.0, '502': 1.0, '503': 1.2, '510': 0.6},
 '1050': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '510': 0.6},
 '1051': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '510': 0.6},
 '1052': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '506': 0.8, '510': 0.6},
 '1053': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '507': 0.8, '510': 0.6},
 '1054': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '506': 0.8, '507': 0.8, '510': 0.6},
 '1055': {'501': 1.0, '502': 1.0, '503': 1.2, '511': 0.6},
 '1056': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '511': 0.6},
 '1057': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '511': 0.6},
 '1058': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '506': 0.8, '511': 0.6},
 '1059': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '507': 0.8, '511': 0.6},
 '1060': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '506': 0.8, '507': 0.8, '511': 0.6},
 '1061': {'501': 1.0, '502': 1.0, '503': 1.2},
 '1062': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0},
 '1063': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0},
 '1064': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '506': 0.0},
 '1065': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '507': 0.0},
 '1066': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '506': 0.0, '507': 0.0},
 '1067': {'501': 1.0, '502': 1.0, '503': 1.2, '508': 0.5},
 '1068': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '508': 0.5},
 '1069': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '508': 0.5},
 '1070': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '506': 0.0, '508': 0.5},
 '1071': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '507': 0.0, '508': 0.5},
 '1072': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '506': 0.0, '507': 0.0, '508': 0.5},
 '1073': {'501': 1.0, '502': 1.0, '503': 1.2, '509': 0.5},
 '1074': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '509': 0.5},
 '1075': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '509': 0.5},
 '1076': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '506': 0.0, '509': 0.5},
 '1077': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '507': 0.0, '509': 0.5},
 '1078': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '506': 0.0, '507': 0.0, '509': 0.5},
 '1079': {'501': 1.0, '502': 1.0, '503': 1.2, '510': 0.5},
 '1080': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '510': 0.5},
 '1081': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '510': 0.5},
 '1082': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '506': 0.0, '510': 0.5},
 '1083': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '507': 0.0, '510': 0.5},
 '1084': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '506': 0.0, '507': 0.0, '510': 0.5},
 '1085': {'501': 1.0, '502': 1.0, '503': 1.2, '511': 0.5},
 '1086': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '511': 0.5},
 '1087': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '511': 0.5},
 '1088': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '506': 0.0, '511': 0.5},
 '1089': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '507': 0.0, '511': 0.5},
 '1090': {'501': 1.0, '502': 1.0, '503': 1.2, '504': 1.0, '505': 1.0, '506': 0.0, '507': 0.0, '511': 0.5}}
SOURCE_END_ROWS = {'Quasi': [[101, 'start', 1],
           [101, 'end', 2],
           [102, 'start', 2],
           [102, 'end', 3],
           [103, 'start', 3],
           [103, 'end', 4],
           [104, 'start', 4],
           [104, 'end', 5],
           [105, 'start', 5],
           [105, 'end', 6],
           [106, 'start', 6],
           [106, 'end', 7],
           [107, 'start', 7],
           [107, 'end', 8],
           [108, 'start', 8],
           [108, 'end', 9],
           [109, 'start', 9],
           [109, 'end', 10],
           [110, 'start', 10],
           [110, 'end', 11],
           [111, 'start', 11],
           [111, 'end', 12],
           [112, 'start', 12],
           [112, 'end', 13],
           [113, 'start', 13],
           [113, 'end', 14],
           [114, 'start', 14],
           [114, 'end', 15],
           [201, 'start', 16],
           [201, 'end', 17],
           [202, 'start', 17],
           [202, 'end', 18],
           [203, 'start', 18],
           [203, 'end', 19],
           [204, 'start', 19],
           [204, 'end', 20],
           [205, 'start', 20],
           [205, 'end', 21],
           [206, 'start', 21],
           [206, 'end', 22],
           [207, 'start', 22],
           [207, 'end', 23],
           [208, 'start', 23],
           [208, 'end', 24],
           [209, 'start', 24],
           [209, 'end', 25],
           [210, 'start', 25],
           [210, 'end', 26],
           [211, 'start', 26],
           [211, 'end', 27],
           [212, 'start', 27],
           [212, 'end', 28],
           [213, 'start', 28],
           [213, 'end', 29],
           [214, 'start', 29],
           [214, 'end', 30],
           [215, 'start', 30],
           [215, 'end', 31],
           [1001, 'start', 1],
           [1001, 'end', 32],
           [1002, 'start', 32],
           [1002, 'end', 33],
           [1003, 'start', 33],
           [1003, 'end', 34],
           [1004, 'start', 34],
           [1004, 'end', 35],
           [1005, 'start', 35],
           [1005, 'end', 36],
           [1006, 'start', 36],
           [1006, 'end', 16],
           [2001, 'start', 15],
           [2001, 'end', 37],
           [2002, 'start', 37],
           [2002, 'end', 38],
           [2003, 'start', 38],
           [2003, 'end', 39],
           [2004, 'start', 39],
           [2004, 'end', 40],
           [2005, 'start', 40],
           [2005, 'end', 41],
           [2006, 'start', 41],
           [2006, 'end', 31]],
 'Rare': [[101, 'start', 1],
          [101, 'end', 2],
          [102, 'start', 2],
          [102, 'end', 3],
          [103, 'start', 3],
          [103, 'end', 4],
          [104, 'start', 4],
          [104, 'end', 5],
          [105, 'start', 5],
          [105, 'end', 6],
          [106, 'start', 6],
          [106, 'end', 7],
          [107, 'start', 7],
          [107, 'end', 8],
          [108, 'start', 8],
          [108, 'end', 9],
          [109, 'start', 9],
          [109, 'end', 10],
          [110, 'start', 10],
          [110, 'end', 11],
          [111, 'start', 11],
          [111, 'end', 12],
          [112, 'start', 12],
          [112, 'end', 13],
          [113, 'start', 13],
          [113, 'end', 14],
          [114, 'start', 14],
          [114, 'end', 15],
          [201, 'start', 16],
          [201, 'end', 17],
          [202, 'start', 17],
          [202, 'end', 18],
          [203, 'start', 18],
          [203, 'end', 19],
          [204, 'start', 19],
          [204, 'end', 20],
          [205, 'start', 20],
          [205, 'end', 21],
          [206, 'start', 21],
          [206, 'end', 22],
          [207, 'start', 22],
          [207, 'end', 23],
          [208, 'start', 23],
          [208, 'end', 24],
          [209, 'start', 24],
          [209, 'end', 25],
          [210, 'start', 25],
          [210, 'end', 26],
          [211, 'start', 26],
          [211, 'end', 27],
          [212, 'start', 27],
          [212, 'end', 28],
          [213, 'start', 28],
          [213, 'end', 29],
          [214, 'start', 29],
          [214, 'end', 30],
          [215, 'start', 30],
          [215, 'end', 31],
          [1001, 'start', 1],
          [1001, 'end', 32],
          [1002, 'start', 32],
          [1002, 'end', 33],
          [1003, 'start', 33],
          [1003, 'end', 34],
          [1004, 'start', 34],
          [1004, 'end', 35],
          [1005, 'start', 35],
          [1005, 'end', 36],
          [1006, 'start', 36],
          [1006, 'end', 16],
          [2001, 'start', 15],
          [2001, 'end', 37],
          [2002, 'start', 37],
          [2002, 'end', 38],
          [2003, 'start', 38],
          [2003, 'end', 39],
          [2004, 'start', 39],
          [2004, 'end', 40],
          [2005, 'start', 40],
          [2005, 'end', 41],
          [2006, 'start', 41],
          [2006, 'end', 31]],
 'ULS': [[101, 'start', 1],
         [101, 'end', 2],
         [102, 'start', 2],
         [102, 'end', 3],
         [103, 'start', 3],
         [103, 'end', 4],
         [104, 'start', 4],
         [104, 'end', 5],
         [105, 'start', 5],
         [105, 'end', 6],
         [106, 'start', 6],
         [106, 'end', 7],
         [107, 'start', 7],
         [107, 'end', 8],
         [108, 'start', 8],
         [108, 'end', 9],
         [109, 'start', 9],
         [109, 'end', 10],
         [110, 'start', 10],
         [110, 'end', 11],
         [111, 'start', 11],
         [111, 'end', 12],
         [112, 'start', 12],
         [112, 'end', 13],
         [113, 'start', 13],
         [113, 'end', 14],
         [114, 'start', 14],
         [114, 'end', 15],
         [201, 'start', 16],
         [201, 'end', 17],
         [202, 'start', 17],
         [202, 'end', 18],
         [203, 'start', 18],
         [203, 'end', 19],
         [204, 'start', 19],
         [204, 'end', 20],
         [205, 'start', 20],
         [205, 'end', 21],
         [206, 'start', 21],
         [206, 'end', 22],
         [207, 'start', 22],
         [207, 'end', 23],
         [208, 'start', 23],
         [208, 'end', 24],
         [209, 'start', 24],
         [209, 'end', 25],
         [210, 'start', 25],
         [210, 'end', 26],
         [211, 'start', 26],
         [211, 'end', 27],
         [212, 'start', 27],
         [212, 'end', 28],
         [213, 'start', 28],
         [213, 'end', 29],
         [214, 'start', 29],
         [214, 'end', 30],
         [215, 'start', 30],
         [215, 'end', 31],
         [1001, 'start', 1],
         [1001, 'end', 32],
         [1002, 'start', 32],
         [1002, 'end', 33],
         [1003, 'start', 33],
         [1003, 'end', 34],
         [1004, 'start', 34],
         [1004, 'end', 35],
         [1005, 'start', 35],
         [1005, 'end', 36],
         [1006, 'start', 36],
         [1006, 'end', 16],
         [2001, 'start', 15],
         [2001, 'end', 37],
         [2002, 'start', 37],
         [2002, 'end', 38],
         [2003, 'start', 38],
         [2003, 'end', 39],
         [2004, 'start', 39],
         [2004, 'end', 40],
         [2005, 'start', 40],
         [2005, 'end', 41],
         [2006, 'start', 41],
         [2006, 'end', 31]]}
MOMENT_SELECTORS = [{'by_family': {'Quasi': [{'beam': 201, 'node': 17, 'sign': 'max', 'source_row': 17},
                          {'beam': 215, 'node': 31, 'sign': 'max', 'source_row': 18}],
                'Rare': [{'beam': 201, 'node': 17, 'sign': 'max', 'source_row': 17},
                         {'beam': 215, 'node': 31, 'sign': 'max', 'source_row': 18}],
                'ULS': [{'beam': 201, 'node': 17, 'sign': 'max', 'source_row': 17},
                        {'beam': 215, 'node': 30, 'sign': 'max', 'source_row': 18}]},
  'excel_row': 215,
  'op': 'max'},
 {'by_family': {'Quasi': [{'beam': 202, 'node': 18, 'sign': 'max', 'source_row': 25},
                          {'beam': 214, 'node': 29, 'sign': 'max', 'source_row': 26}],
                'Rare': [{'beam': 202, 'node': 18, 'sign': 'max', 'source_row': 25},
                         {'beam': 214, 'node': 29, 'sign': 'max', 'source_row': 26}],
                'ULS': [{'beam': 202, 'node': 18, 'sign': 'max', 'source_row': 25},
                        {'beam': 214, 'node': 29, 'sign': 'max', 'source_row': 26}]},
  'excel_row': 216,
  'op': 'max'},
 {'by_family': {'Quasi': [{'beam': 207, 'node': 23, 'sign': 'min', 'source_row': 9}],
                'Rare': [{'beam': 207, 'node': 23, 'sign': 'min', 'source_row': 9}],
                'ULS': [{'beam': 207, 'node': 23, 'sign': 'min', 'source_row': 9}]},
  'excel_row': 217,
  'op': 'min'},
 {'by_family': {'Quasi': [{'beam': 101, 'node': 2, 'sign': 'min', 'source_row': 33},
                          {'beam': 114, 'node': 14, 'sign': 'min', 'source_row': 34}],
                'Rare': [{'beam': 101, 'node': 2, 'sign': 'min', 'source_row': 33},
                         {'beam': 114, 'node': 14, 'sign': 'min', 'source_row': 34}],
                'ULS': [{'beam': 101, 'node': 2, 'sign': 'min', 'source_row': 33},
                        {'beam': 114, 'node': 14, 'sign': 'min', 'source_row': 34}]},
  'excel_row': 220,
  'op': 'min'},
 {'by_family': {'Quasi': [{'beam': 102, 'node': 3, 'sign': 'min', 'source_row': 41},
                          {'beam': 113, 'node': 13, 'sign': 'min', 'source_row': 42}],
                'Rare': [{'beam': 102, 'node': 3, 'sign': 'min', 'source_row': 41},
                         {'beam': 113, 'node': 13, 'sign': 'min', 'source_row': 42}],
                'ULS': [{'beam': 102, 'node': 3, 'sign': 'min', 'source_row': 41},
                        {'beam': 113, 'node': 13, 'sign': 'min', 'source_row': 42}]},
  'excel_row': 221,
  'op': 'min'},
 {'by_family': {'Quasi': [{'beam': 108, 'node': 8, 'sign': 'max', 'source_row': 13}],
                'Rare': [{'beam': 108, 'node': 8, 'sign': 'max', 'source_row': 13}],
                'ULS': [{'beam': 108, 'node': 8, 'sign': 'max', 'source_row': 13}]},
  'excel_row': 222,
  'op': 'max'},
 {'by_family': {'Quasi': [{'beam': 1006, 'node': 36, 'sign': 'max', 'source_row': 49}],
                'Rare': [{'beam': 1006, 'node': 36, 'sign': 'max', 'source_row': 49}],
                'ULS': [{'beam': 1006, 'node': 36, 'sign': 'max', 'source_row': 49}]},
  'excel_row': 225,
  'op': 'single'},
 {'by_family': {'Quasi': [{'beam': 2006, 'node': 41, 'sign': 'min', 'source_row': 54}],
                'Rare': [{'beam': 2006, 'node': 41, 'sign': 'min', 'source_row': 54}],
                'ULS': [{'beam': 2006, 'node': 41, 'sign': 'min', 'source_row': 54}]},
  'excel_row': 226,
  'op': 'single'},
 {'by_family': {'Quasi': [{'beam': 1006, 'node': 36, 'sign': 'min', 'source_row': 50}],
                'Rare': [{'beam': 1006, 'node': 36, 'sign': 'min', 'source_row': 50}],
                'ULS': [{'beam': 1006, 'node': 36, 'sign': 'min', 'source_row': 50}]},
  'excel_row': 227,
  'op': 'single'},
 {'by_family': {'Quasi': [{'beam': 2006, 'node': 41, 'sign': 'max', 'source_row': 53}],
                'Rare': [{'beam': 2006, 'node': 41, 'sign': 'max', 'source_row': 53}],
                'ULS': [{'beam': 2006, 'node': 41, 'sign': 'max', 'source_row': 53}]},
  'excel_row': 228,
  'op': 'single'},
 {'by_family': {'Quasi': [{'beam': 1005, 'node': 35, 'sign': 'max', 'source_row': 51}],
                'Rare': [{'beam': 1005, 'node': 35, 'sign': 'max', 'source_row': 51}],
                'ULS': [{'beam': 1005, 'node': 35, 'sign': 'max', 'source_row': 51}]},
  'excel_row': 229,
  'op': 'single'},
 {'by_family': {'Quasi': [{'beam': 2005, 'node': 40, 'sign': 'min', 'source_row': 56}],
                'Rare': [{'beam': 2005, 'node': 40, 'sign': 'min', 'source_row': 56}],
                'ULS': [{'beam': 2005, 'node': 40, 'sign': 'min', 'source_row': 56}]},
  'excel_row': 230,
  'op': 'single'},
 {'by_family': {'Quasi': [{'beam': 1005, 'node': 35, 'sign': 'min', 'source_row': 52}],
                'Rare': [{'beam': 1005, 'node': 35, 'sign': 'min', 'source_row': 52}],
                'ULS': [{'beam': 1005, 'node': 35, 'sign': 'min', 'source_row': 52}]},
  'excel_row': 231,
  'op': 'single'},
 {'by_family': {'Quasi': [{'beam': 2005, 'node': 40, 'sign': 'max', 'source_row': 55}],
                'Rare': [{'beam': 2005, 'node': 40, 'sign': 'max', 'source_row': 55}],
                'ULS': [{'beam': 2005, 'node': 40, 'sign': 'max', 'source_row': 55}]},
  'excel_row': 232,
  'op': 'single'},
 {'by_family': {'Quasi': [{'beam': 1004, 'node': 34, 'sign': 'max', 'source_row': 69}],
                'Rare': [{'beam': 1004, 'node': 34, 'sign': 'max', 'source_row': 69}],
                'ULS': [{'beam': 1004, 'node': 34, 'sign': 'max', 'source_row': 69}]},
  'excel_row': 233,
  'op': 'single'},
 {'by_family': {'Quasi': [{'beam': 2004, 'node': 39, 'sign': 'min', 'source_row': 72}],
                'Rare': [{'beam': 2004, 'node': 39, 'sign': 'min', 'source_row': 72}],
                'ULS': [{'beam': 2004, 'node': 39, 'sign': 'min', 'source_row': 72}]},
  'excel_row': 234,
  'op': 'single'},
 {'by_family': {'Quasi': [{'beam': 1004, 'node': 34, 'sign': 'min', 'source_row': 70}],
                'Rare': [{'beam': 1004, 'node': 34, 'sign': 'min', 'source_row': 70}],
                'ULS': [{'beam': 1004, 'node': 34, 'sign': 'min', 'source_row': 70}]},
  'excel_row': 235,
  'op': 'single'},
 {'by_family': {'Quasi': [{'beam': 2004, 'node': 39, 'sign': 'max', 'source_row': 71}],
                'Rare': [{'beam': 2004, 'node': 39, 'sign': 'max', 'source_row': 71}],
                'ULS': [{'beam': 2004, 'node': 39, 'sign': 'max', 'source_row': 71}]},
  'excel_row': 236,
  'op': 'single'},
 {'by_family': {'Quasi': [{'beam': 1002, 'node': 32, 'sign': 'max', 'source_row': 79}],
                'Rare': [{'beam': 1002, 'node': 32, 'sign': 'max', 'source_row': 79}],
                'ULS': [{'beam': 1002, 'node': 32, 'sign': 'max', 'source_row': 79}]},
  'excel_row': 237,
  'op': 'single'},
 {'by_family': {'Quasi': [{'beam': 2002, 'node': 37, 'sign': 'min', 'source_row': 84}],
                'Rare': [{'beam': 2002, 'node': 37, 'sign': 'min', 'source_row': 84}],
                'ULS': [{'beam': 2002, 'node': 37, 'sign': 'min', 'source_row': 84}]},
  'excel_row': 238,
  'op': 'single'},
 {'by_family': {'Quasi': [{'beam': 1002, 'node': 32, 'sign': 'min', 'source_row': 80}],
                'Rare': [{'beam': 1002, 'node': 32, 'sign': 'min', 'source_row': 80}],
                'ULS': [{'beam': 1002, 'node': 32, 'sign': 'min', 'source_row': 80}]},
  'excel_row': 239,
  'op': 'single'},
 {'by_family': {'Quasi': [{'beam': 2002, 'node': 37, 'sign': 'max', 'source_row': 83}],
                'Rare': [{'beam': 2002, 'node': 37, 'sign': 'max', 'source_row': 83}],
                'ULS': [{'beam': 2002, 'node': 37, 'sign': 'max', 'source_row': 83}]},
  'excel_row': 240,
  'op': 'single'},
 {'by_family': {'Quasi': [{'beam': 1003, 'node': 33, 'sign': 'max', 'source_row': 81}],
                'Rare': [{'beam': 1003, 'node': 33, 'sign': 'max', 'source_row': 81}],
                'ULS': [{'beam': 1003, 'node': 33, 'sign': 'max', 'source_row': 81}]},
  'excel_row': 241,
  'op': 'single'},
 {'by_family': {'Quasi': [{'beam': 2003, 'node': 38, 'sign': 'min', 'source_row': 86}],
                'Rare': [{'beam': 2003, 'node': 38, 'sign': 'min', 'source_row': 86}],
                'ULS': [{'beam': 2003, 'node': 38, 'sign': 'min', 'source_row': 86}]},
  'excel_row': 242,
  'op': 'single'},
 {'by_family': {'Quasi': [{'beam': 1003, 'node': 33, 'sign': 'min', 'source_row': 82}],
                'Rare': [{'beam': 1003, 'node': 33, 'sign': 'min', 'source_row': 82}],
                'ULS': [{'beam': 1003, 'node': 33, 'sign': 'min', 'source_row': 82}]},
  'excel_row': 243,
  'op': 'single'},
 {'by_family': {'Quasi': [{'beam': 2003, 'node': 38, 'sign': 'max', 'source_row': 85}],
                'Rare': [{'beam': 2003, 'node': 38, 'sign': 'max', 'source_row': 85}],
                'ULS': [{'beam': 2003, 'node': 38, 'sign': 'max', 'source_row': 85}]},
  'excel_row': 244,
  'op': 'single'}]

CASE_TO_COMPONENT = {501: 'concrete_dead_factor', 502: 'additional_sidl_factor',
                     503: 'wearing_course_factor', 504: 'soil_factor',
                     505: 'cushion_factor', 506: 'surcharge_factor',
                     507: 'surcharge_factor'}
SOURCE_BEAM_GROUPS = (
    ('Bottom slab', range(101,115)),
    ('Top slab', range(201,216)),
    ('Left wall', range(1001,1007)),
    ('Right wall', range(2001,2007)),
)
FAMILY_CASES = {'ULS': range(1001,1031),
                'Rare': range(1031,1061),
                'Quasi': range(1061,1091)}
EXPECTED_EXCEL_ROWS=(215,216,217,220,221,222,*range(225,245))


def _single_case_combo(number):
    if number not in CASE_TO_COMPONENT:
        raise ValueError(f'Unknown static load case {number}')
    params=dict(concrete_dead_factor=0.0,additional_sidl_factor=0.0,
                wearing_course_factor=0.0,soil_factor=0.0,
                cushion_factor=0.0,surcharge_factor=0.0,
                vehicle_factor=0.0,braking_factor=0.0,
                surcharge_side='none')
    params[CASE_TO_COMPONENT[number]]=1.0
    if number in (506,507):
        params['surcharge_side']='left' if number==506 else 'right'
    return CombinationInputs(name=f'Static case {number}',**params)


def _source_beam_mapping(elements):
    ids={}
    for e in elements:
        ids.setdefault(e['member'],[]).append(int(e['element']))
    out={}
    for group,source_numbers in SOURCE_BEAM_GROUPS:
        found=ids.get(group,[])
        numbers=list(source_numbers)
        if len(found)!=len(numbers):
            raise ValueError(f'Cannot match {group}: mesh has {len(found)} sections; '
                             f'original table needs {len(numbers)}. '
                             'This geometry/mesh has not been validated for the source mapping.')
        for beam, eid in zip(numbers,found):
            out[(beam,'start')]=(group,eid,'start')
            out[(beam,'end')]=(group,eid,'end')
    if len(out)!=82:
        raise ValueError('Source model must contain exactly 82 member ends')
    return out


def _family_envelopes(solved):
    mapping=_source_beam_mapping(solved[501]['elements'])
    indexes={number: {(x['member'],int(x['element']),x['end']): x
                       for x in case['member_end_actions']}
             for number,case in solved.items()}
    answers={}
    for family,case_numbers in FAMILY_CASES.items():
        rows={}
        for beam, end, node in SOURCE_END_ROWS[family]:
            key=mapping[(beam,end)]
            sign=1.0 if end=='start' else -1.0
            values=[]
            for case_number in case_numbers:
                factors=CASE_FACTOR_DEFINITIONS[str(case_number)]
                moment_knm=0.0
                for load_number, factor in factors.items():
                    load=int(load_number)
                    if load in (508,509,510,511):
                        # Original reference records zero applied temperature
                        # for these cases. Nonzero thermal actions are NOT modeled.
                        continue
                    if load not in indexes:
                        raise ValueError(f'Unknown source static case: {load}')
                    moment_knm += float(factor)*float(indexes[load][key]['moment_knm'])
                values.append(sign*moment_knm/TF_TO_KN)
            if not values or not all(math.isfinite(x) for x in values):
                raise ValueError('Invalid static section moment')
            rows[(beam,node)]={'min':min(0.0,min(values)),
                               'max':max(0.0,max(values))}
        if len(rows)!=82:
            raise ValueError(f'Incomplete {family} member-end envelope')
        answers[family]=rows
    return answers


def _selected_moment(selector, envelope, family):
    readings=[]
    for item in selector['by_family'][family]:
        key=(int(item['beam']),int(item['node']))
        if key not in envelope[family]:
            raise ValueError(f'Missing source end {key} in {family}')
        readings.append(float(envelope[family][key][item['sign']]))
    operator=selector['op']
    if operator=='max':return max(readings)
    if operator=='min':return min(readings)
    if operator=='max_abs':return max(abs(n) for n in readings)
    if operator=='single' and len(readings)==1:return readings[0]
    raise ValueError(f'Unsupported source selector {operator!r}')


def calculate_connected_normal_moments(inputs, *, options=None):
    """Return 26 dynamically calculated static Normal component dictionaries.

    All seven independent FEM cases use the supplied active Design Inputs.
    Frame mesh is fixed at the validated source-station resolution (8) to
    preserve member-end identity, independent of Stage 4B display mesh.
    Changing physical input geometry/material/earth parameters re-solves FEM.
    Live-load result fields are NOT created or modified.
    """
    inputs.validate()
    if not inputs.supports_single_cell_model:
        raise ValueError('Only a single-cell culvert is supported')
    selected=options if options is not None else FrameOptions()
    if selected.compression_only_springs:
        raise ValueError('The static load-combination envelope requires linear bilateral supports; '
                         'disable compression-only springs for this table')
    options=replace(selected,mesh_divisions_per_member=8)
    earth=EarthPressureInputs(
        k0=float(inputs.earth_pressure_coefficient_k0),
        backfill_density_t_m3=float(inputs.backfill_density_tf_m3),
        surcharge_equivalent_height_m=float(inputs.surcharge_equivalent_height_m),
        earth_cushion_m=float(inputs.earth_cushion_m))
    solved={}
    for number in range(501,508):
        solution=run_frame_analysis(inputs,earth,None,None,_single_case_combo(number),options)
        if not solution.get('converged') or not solution.get('member_end_actions'):
            raise ValueError(f'Static FEM load case {number} did not converge')
        solved[number]=solution
    families=_family_envelopes(solved)
    output=[]
    if tuple(s['excel_row'] for s in MOMENT_SELECTORS)!=EXPECTED_EXCEL_ROWS:
        raise ValueError('Original 26 design-moment source row order changed')
    for s in MOMENT_SELECTORS:
        row={'Excel row':int(s['excel_row'])}
        for family in ('ULS','Rare','Quasi'):
            row[f'{family} Normal']=_selected_moment(s,families,family)
        output.append(row)
    return output



# The following row selectors reproduce only the original worksheet's
# section/beam identification rules. No source numeric shear results are used.
# Each tuple: Excel row, selector op, ((beam, node, signed bound), ...).
SHEAR_SELECTORS = (
    (259, 'max_abs', ((201, 17, 'max'), (210, 25, 'min'))),
    (260, 'max_abs', ((202, 18, 'max'), (209, 24, 'min'))),
    (261, 'max_abs', ((206, 22, 'min'),)),
    (264, 'max_abs', ((101, 2, 'min'), (114, 14, 'max'))),
    (265, 'max_abs', ((102, 3, 'min'), (113, 13, 'max'))),
    (266, 'max_abs', ((109, 9, 'max'),)),
    (269, 'max_abs', ((1006, 36, 'max'), (1006, 36, 'min'))),
    (270, 'max_abs', ((2006, 41, 'max'), (2006, 41, 'min'))),
    (271, 'max_abs', ((1005, 35, 'max'), (1005, 35, 'min'))),
    (272, 'max_abs', ((2005, 40, 'max'), (2005, 40, 'min'))),
    (273, 'max_abs', ((1004, 34, 'max'), (1004, 34, 'min'))),
    (274, 'max_abs', ((2004, 39, 'max'), (2004, 39, 'min'))),
    (275, 'max_abs', ((1002, 32, 'max'), (1002, 32, 'min'))),
    (276, 'max_abs', ((2002, 37, 'max'), (2002, 37, 'min'))),
    (277, 'max_abs', ((1003, 33, 'max'), (1003, 33, 'min'))),
    (278, 'max_abs', ((2003, 38, 'max'), (2003, 38, 'min'))),
)
EXPECTED_SHEAR_EXCEL_ROWS = (259, 260, 261, 264, 265, 266, *range(269, 279))


def calculate_connected_static_actions(inputs, *, options=None):
    """Compute 26 Normal moment rows and 16 Normal shear rows from live FEM.

    The original workbook LL fields are deliberately NOT generated: vehicle
    member forces remain unvalidated. Both component lists are computed from
    the exact same seven independent FEM cases to avoid inconsistent demands.
    """
    inputs.validate()
    if not inputs.supports_single_cell_model:
        raise ValueError('Only a single-cell culvert is supported')
    selected = options if options is not None else FrameOptions()
    if selected.compression_only_springs:
        raise ValueError('Static envelopes need linear bilateral supports')
    solver_options = replace(selected, mesh_divisions_per_member=8)
    earth = EarthPressureInputs(
        k0=float(inputs.earth_pressure_coefficient_k0),
        backfill_density_t_m3=float(inputs.backfill_density_tf_m3),
        surcharge_equivalent_height_m=float(inputs.surcharge_equivalent_height_m),
        earth_cushion_m=float(inputs.earth_cushion_m),
    )
    solved = {}
    for number in range(501, 508):
        solution = run_frame_analysis(
            inputs, earth, None, None, _single_case_combo(number), solver_options
        )
        if not solution.get('converged') or not solution.get('member_end_actions'):
            raise ValueError(f'Static FEM load case {number} failed')
        solved[number] = solution
    families = _family_envelopes(solved)
    moments = []
    if tuple(s['excel_row'] for s in MOMENT_SELECTORS) != EXPECTED_EXCEL_ROWS:
        raise ValueError('Original 26 moment selector order changed')
    for selector in MOMENT_SELECTORS:
        row = {'Excel row': int(selector['excel_row'])}
        for family in ('ULS', 'Rare', 'Quasi'):
            row[f'{family} Normal'] = _selected_moment(selector, families, family)
        moments.append(row)

    # Shear uses ULS combination cases and the SAME section-orientation
    # convention as bending. Shears are measured as forces per metre strip.
    mapping = _source_beam_mapping(solved[501]['elements'])
    indexes = {
        number: {(action['member'], int(action['element']), action['end']): action
                 for action in solution['member_end_actions']}
        for number, solution in solved.items()
    }
    shear_envelopes = {}
    for beam, end, node in SOURCE_END_ROWS['ULS']:
        key = mapping[(beam, end)]
        orientation = 1.0 if end == 'start' else -1.0
        values = []
        for case_number in FAMILY_CASES['ULS']:
            total = sum(
                float(factor) * float(indexes[int(load)][key]['shear_kn'])
                for load, factor in CASE_FACTOR_DEFINITIONS[str(case_number)].items()
                if 501 <= int(load) <= 507
            )
            values.append(orientation * total / TF_TO_KN)
        if not values or not all(math.isfinite(value) for value in values):
            raise ValueError('Invalid static ULS shear envelope')
        shear_envelopes[(beam, node)] = {
            'max': max(0.0, max(values)),
            'min': min(0.0, min(values)),
        }
    if len(shear_envelopes) != 82:
        raise ValueError('Incomplete source member-end shear mapping')
    if tuple(row[0] for row in SHEAR_SELECTORS) != EXPECTED_SHEAR_EXCEL_ROWS:
        raise ValueError('Original 16 shear selector order changed')
    shears = []
    for excel_row, operator, selectors in SHEAR_SELECTORS:
        readings = [float(shear_envelopes[(beam, node)][bound])
                    for beam, node, bound in selectors]
        if operator != 'max_abs' or not readings:
            raise ValueError('Unsupported source shear selector')
        shears.append({'Excel row': excel_row, 'Normal': max(abs(n) for n in readings)})
    return {'moment_normal': moments, 'shear_normal': shears}
