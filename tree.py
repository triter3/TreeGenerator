import os
import sys
import math
from  mathutils import Vector
from  mathutils import Quaternion
import bpy
import bmesh
from time import time
from random import random

# Add the blender path to path list
dir = os.path.dirname(bpy.data.filepath)
if not dir in sys.path:
    sys.path.append(dir )
    
from lark import Lark

lSystemGrammar = """
start: "loose:" BOOL rulecall rule+ -> rule_set

rule: NAME "(" rulearguments ")" ":" rulebody ";" -> rule

rulearguments: (NAME (","NAME)*)? -> rule_arguments

rulebody: instructions+ -> rule_body

instructions: rulecall        -> rule_call_inst
            | "$" "(" sum ")" -> pitch_inst
            | "%" "(" sum ")" -> roll_inst
            | "^" "(" sum "," sum ")" -> create_branch
            | "'" "(" sum "," sum ")" -> create_leaf
            | "["             -> push_inst
            | "]"             -> pop_inst
            | "if" binaryop ":" rulebody ("else" ":" rulebody)? ";" -> cond_inst

rulecall: NAME "(" (sum (","sum)*)? ")" -> rule_call

?binaryop: cond
        | "(" cond ")"
        | binaryop "and" binaryop -> and 
        | binaryop "or" binaryop -> or
        | "not" binaryop -> not

?cond: sum "==" sum -> eq
    | sum "!=" sum -> neq
    | sum "<" sum -> lt
    | sum "<=" sum -> let
    | sum ">" sum -> gt
    | sum ">=" sum -> get

?sum: product
    | sum "+" product   -> add
    | sum "-" product   -> sub

?product: atom
    | product "*" atom  -> mul
    | product "/" atom  -> div

?atom: NUMBER        -> number
     | "-" atom      -> neg
     | NAME          -> var
     | atom "~" atom -> noise
     | "(" sum ")"
         
NUMBER : /-?\d+(\.\d+)?([eE][+-]?\d+)?/
NAME : /[A-Za-z]+//[A-Za-z1-9]*/
BOOL: "True" | "False"

%import common.WS
%ignore WS

"""

parser = Lark(lSystemGrammar)

class LSystem:
    def __init__(self, text):
        parse_tree = parser.parse(text)
        
        self.useLoose = parse_tree.children[0].value == "True"
            
        self.axiom = parse_tree.children[1]
        self.rules = {}
        for c in parse_tree.children[2:]:
            ruleName, arguments, body = c.children
            self.rules[ruleName.value] = (arguments.children, body.children)
    
    def smoothTree(self, obj):
        if self.useLoose:
            for i in range(len(self.vertices)):
                obj.data.skin_vertices[0].data[i].use_loose = True
                
            edgeCount = {}
            for v1, v2 in self.edges:
                if v1 not in edgeCount:
                    edgeCount[v1] = v2
                else:
                    edgeCount[v1] = None
                
                if v2 not in edgeCount:
                    edgeCount[v2] = v1
                else:
                    edgeCount[v2] = None
                    
            for v, c in edgeCount.items():
                if c is not None:
                    # Clear leaf
                    obj.data.skin_vertices[0].data[v].use_loose = False
                    # Clear parent
                    obj.data.skin_vertices[0].data[c].use_loose = False
        
        # Apply filters
        bpy.ops.object.modifier_apply(modifier="Skin")
        bpy.ops.object.modifier_apply(modifier="Subdivision")
        
        # Smooth normals
        bpy.ops.object.shade_smooth()

                
    def createMesh(self):
        # Create a mesh
        newMesh = bpy.data.meshes.new("TreeMesh")
        newMetaBalls = bpy.data.metaballs.new("TreeLeaves")
        newObject = bpy.data.objects.new("Tree", newMesh)
        newObject1 = bpy.data.objects.new("Leaves", newMetaBalls)
        
        bpy.context.scene.collection.objects.link(newObject)
        bpy.context.scene.collection.objects.link(newObject1)
        
        # Select object
        bpy.ops.object.select_all(action='DESELECT')
        newObject.select_set(True)
        bpy.context.view_layer.objects.active = newObject
        
        newMesh.vertices.add(len(self.vertices))
        i = 0
        for pos,_ in self.vertices:
            newMesh.vertices[i].co = pos
            i += 1
        
        newMesh.edges.add(len(self.edges))
        i = 0
        for e in self.edges:
            newMesh.edges[i].vertices = e
            i += 1
        
        # Apply modifiers 
        bpy.ops.object.modifier_add(type='SKIN')
        bpy.context.object.modifiers["Skin"].use_x_symmetry = False
        bpy.ops.object.modifier_add(type='SUBSURF')
        bpy.context.object.modifiers["Subdivision"].levels = 3
        
        i = 0    
        for _, w in self.vertices:
            newObject.data.skin_vertices[0].data[i].radius = (w, w)
            i += 1
            
        self.smoothTree(newObject)
        
        # Create leaves
        i = 0
        for v, r in self.leaves:
            newMetaBalls.elements.new()
            newMetaBalls.elements[i].co = v
            newMetaBalls.elements[i].radius = r
            i += 1
            
        
    def exec(self):
        # Turtle init position
        self.turtlePos = Vector((0, 0, 0))
        self.turtleQuat = Quaternion()
        self.vertices = [(self.turtlePos, 1)]
        self.currentVertexIndex = 0
        self.edges = []
        self.leaves = []
        self.turtleQueue = []
        
        self.evalRule(self.axiom, {})
        
        self.vertices[0] = (self.vertices[0][0], self.vertices[1][1])
        
        self.createMesh()
    
    ### Parser functions ###
    
    # Process rule_calls
    def evalRule(self, node, ctx):
        newCtx = {}
        ruleName = node.children[0]
        
        # Search the rule
        ruleDef = self.rules[ruleName]
        
        # Merge the arguments
        if len(ruleDef[0]) != len(node.children)-1:
            raise Exception(r'rule {} wrong number of arguments'.format(ruleName))
        
        i = 0
        for name in ruleDef[0]:
            newCtx[name.value] = self.evalSum(node.children[i+1], ctx)
            i += 1
            
        # Process body 
        for inst in ruleDef[1]:
            self.evalInstruction(inst, newCtx)
        
    # Process instructions
    def evalInstruction(self, node, ctx):
        if node.data == "rule_call_inst":
            self.evalRule(node.children[0], ctx)
        elif node.data == "pitch_inst":
            self.pitch(self.evalSum(node.children[0], ctx))
        elif node.data == "roll_inst":
            self.roll(self.evalSum(node.children[0], ctx))
        elif node.data == "create_branch":
            self.createBranch(self.evalSum(node.children[0], ctx), self.evalSum(node.children[1], ctx))
        elif node.data == "create_leaf":
            self.createLeaf(self.evalSum(node.children[0], ctx), self.evalSum(node.children[1], ctx))
        elif node.data == "push_inst":
            self.pushPos()
        elif node.data == "pop_inst":
            self.popPos()
        elif node.data == "cond_inst":
            if self.evalCondition(node.children[0], ctx):
                for inst in node.children[1].children:
                    self.evalInstruction(inst, ctx)
            elif len(node.children) > 2:
                for inst in node.children[2].children:
                    self.evalInstruction(inst, ctx)
                            
    # Process binaryOp
    def evalCondition(self, node, ctx):
        if node.data == "and":
            return self.evalCondition(node.children[0], ctx) and self.evalCondition(node.children[1], ctx)
        if node.data == "or":
            return self.evalCondition(node.children[0], ctx) or self.evalCondition(node.children[1], ctx)
        if node.data == "not":
            return not self.evalCondition(node.children[0], ctx)
        if node.data == "eq":
            return self.evalSum(node.children[0], ctx) == self.evalSum(node.children[1], ctx)
        if node.data == "neq":
            return self.evalSum(node.children[0], ctx) != self.evalSum(node.children[1], ctx)
        if node.data == "lt":
            return self.evalSum(node.children[0], ctx) < self.evalSum(node.children[1], ctx)
        if node.data == "let":
            return self.evalSum(node.children[0], ctx) <= self.evalSum(node.children[1], ctx)
        if node.data == "gt":
            return self.evalSum(node.children[0], ctx) > self.evalSum(node.children[1], ctx)
        if node.data == "get":
            return self.evalSum(node.children[0], ctx) >= self.evalSum(node.children[1], ctx)
    
    # Process sum
    def evalSum(self, node, ctx):
        if node.data == "add":
            return self.evalSum(node.children[0], ctx) + self.evalSum(node.children[1], ctx)
        if node.data == "sub":
            return self.evalSum(node.children[0], ctx) - self.evalSum(node.children[1], ctx)
        if node.data == "mul":
            return self.evalSum(node.children[0], ctx) * self.evalSum(node.children[1], ctx)
        if node.data == "div":
            return self.evalSum(node.children[0], ctx) / self.evalSum(node.children[1], ctx)
        if node.data == "number":
            return float(node.children[0].value) 
        if node.data == "neg":
            return -self.evalSum(node.children[0], ctx)
        if node.data == "var":
            varName = node.children[0].value
            if varName in ctx:
                return ctx[varName]
            else:
                raise Exception(r'variable {} not defined'.format(varName))
        if node.data == "noise":
            return self.evalSum(node.children[0], ctx) + self.evalSum(node.children[1], ctx)*(random()*2.0 - 1.0)        
    
    ### Turtle functions ###        
    def pitch(self, angle):
        self.turtleQuat = self.turtleQuat @ Quaternion(self.turtleQuat.inverted().to_matrix() @ Vector((1, 0, 0)), math.radians(angle))
        
    def roll(self, angle):
        self.turtleQuat = self.turtleQuat @ Quaternion(self.turtleQuat.inverted().to_matrix() @ Vector((0, 0, 1)), math.radians(angle))
        
    def createBranch(self, length, radius):
        self.turtlePos = self.turtlePos + (self.turtleQuat.inverted().to_matrix() @ Vector((0, 0, 1)))*length
        self.vertices.append((self.turtlePos, radius))
        self.edges.append((self.currentVertexIndex, len(self.vertices)-1))
        self.currentVertexIndex = len(self.vertices)-1
        
    def createLeaf(self, length, radius):
        self.turtlePos = self.turtlePos + (self.turtleQuat.inverted().to_matrix() @ Vector((0, 0, 1)))*length
        self.leaves.append((self.turtlePos, radius))
    
    def pushPos(self):
        self.turtleQueue.append((self.currentVertexIndex, self.turtlePos.copy(), self.turtleQuat.copy()))
    
    def popPos(self):
        self.currentVertexIndex, self.turtlePos, self.turtleQuat = self.turtleQueue.pop()
        

for itm in bpy.context.scene.objects:
    itm.hide_viewport = False
    itm.select_set(True)
    bpy.ops.object.delete(use_global=False, confirm=False)


treeWithoutLeaves = """
    loose: True
    init(5, 1, 10)
    init(d, w, l):
        ^(0.1, 4)^(0.1, w)^(l, w)branch(d-1, w*0.7, l*0.8)
    ;
    branch(d, w, l):
        if d > 0:
            if d < 3: [^(l, w)branch(d-1, w*0.71, l*0.72)];
            %(120~(d*3))[$((60*(1.1 - w))~10)^(l, w)branch(d-1, w*(0.7~0.08), l*(0.7~0.12))]
            %(120~(d*3))[$((60*(1.1 - w))~10)^(l, w)branch(d-1, w*(0.7~0.08), l*(0.7~0.12))]
            %(120~(d*3))[$((60*(1.1 - w))~10)^(l, w)branch(d-1, w*(0.7~0.08), l*(0.7~0.12))]
        ;
    ;   
"""

treeWithLeaves = """
    loose: True
    init(7, 1, 10)
    init(d, w, l):
        ^(0.1, 4)^(0.1, w)^(l, w)branch(d-1, w*0.7, l*0.8)
    ;
    branch(d, w, l):
        if d > 0:
            if d < 3: [^(l, w)branch(d-1, w*0.71, l*0.72)];
            %(120~(d*3))[$((60*(1.1 - w))~10)^(l, w)branch(d-1, w*(0.7~0.08), l*(0.7~0.12))]
            %(120~(d*3))[$((60*(1.1 - w))~10)^(l, w)branch(d-1, w*(0.7~0.08), l*(0.7~0.12))]
            %(120~(d*3))[$((60*(1.1 - w))~10)^(l, w)branch(d-1, w*(0.7~0.08), l*(0.7~0.12))]
        else:
            '(0, 1.5~(1.5*0.2))
            leaf(1, 0.8, 1)
        ;
    ;
    leaf(d, m, w):
        if d > 0:
            %(120)[$(90)'(m, w~(w*0.2))leaf(d-1, m*0.7, w*0.8)]
            %(120)[$(90)'(m, w~(w*0.2))leaf(d-1, m*0.7, w*0.8)]
            %(120)[$(90)'(m, w~(w*0.2))leaf(d-1, m*0.7, w*0.8)]
        ;
    ;   
"""

onlyBranches = """
    loose: False
    init(10, 4)
    init(d, l):
        ^(0.1, 1)^(2.5*l, 1 - 1/(d+1))mainBranch(d, 1 - 2/(d+2), 1/(d+2), l)
    ;
    mainBranch(d, w, alpha, l):
        if d > 0:
            %(0~180)
            %(120~10)[$(95~5)lateralBranches(d*0.5, w*(0.6~0.1), d*(0.5~0.03) + 0.3)]
            %(120~10)[$(95~5)lateralBranches(d*0.5, w*(0.6~0.1), d*(0.5~0.03) + 0.3)]
            %(120~10)[$(95~5)lateralBranches(d*0.5, w*(0.6~0.1), d*(0.5~0.03) + 0.3)]
            ^(l, w)
            mainBranch(d-1, w - alpha, alpha, l*0.98)
        ;
    ;
    lateralBranches(d, w, l):
        if d > 0:
            ^(l, w)
            [%(85)$(85~5)%(-85) lateralBranches((d-1)*0.5, w*(0.45~0.1), l*(0.6~0.1))]
            [%(-85)$(85~5)%(85) lateralBranches((d-1)*0.5, w*(0.45~0.1), l*(0.6~0.1))]            
            lateralBranches(d-1, w*(0.75~0.1), l*(0.75~0.1))
        else:
            ^(l, w)
        ;
    ;
"""

bushWithoutLeaves = """
    loose: False
    init(7, 4)
    init(d, l):
        ^(0.1, 1)^(2.5*l, 1 - 1/(d+1))mainBranch(d, 1 - 2/(d+2), 1/(d+2), l)
    ;
    mainBranch(d, w, alpha, l):
        if d > 0:
            %(0~180)
            %(90~10)[$(95~5)lateralBranches1(d*0.5, w*(0.2~0.1), d*(0.35~0.03) + 0.3)]
            %(90~10)[$(95~5)lateralBranches1(d*0.5, w*(0.2~0.1), d*(0.35~0.03) + 0.3)]
            %(90~10)[$(95~5)lateralBranches1(d*0.5, w*(0.2~0.1), d*(0.35~0.03) + 0.3)]
            %(90~10)[$(95~5)lateralBranches1(d*0.5, w*(0.2~0.1), d*(0.35~0.03) + 0.3)]
            ^(l, w)
            mainBranch(d-1, w - alpha, alpha, l*0.98)
        ;
    ;
    lateralBranches1(d, w, l):
        if d > 0:
            ^(l*0.8, w)lateralBranches(d, w, l)
        ;
    ;
    lateralBranches(d, w, l):
        if d > 0:
            if d < 2: [^(l, w)lateralBranches(d-1, w*0.55, l*(0.65~0.12))];
            %(120~(d*3))[$((30*(1.1 - w))~10)^(l, w)lateralBranches(d-1, w*(0.55~0.1), l*(0.65~0.12))]
            %(120~(d*3))[$((30*(1.1 - w))~10)^(l, w)lateralBranches(d-1, w*(0.55~0.1), l*(0.65~0.12))]
            %(120~(d*3))[$((30*(1.1 - w))~10)^(l, w)lateralBranches(d-1, w*(0.55~0.1), l*(0.65~0.12))]
        ;
    ;
"""

bushWithLeaves = """
    loose: False
    init(7, 3)
    init(d, l):
        ^(0.1, 1)^(2.5*l, 1 - 1/(d+1))mainBranch(d, 1 - 2/(d+2), 1/(d+2), l)
    ;
    mainBranch(d, w, alpha, l):
        if d > 0:
            %(0~180)
            %(90~10)[$(95~5)lateralBranches1(d*0.5, w*(0.2~0.1), d*(0.35~0.03) + 0.3)]
            %(90~10)[$(95~5)lateralBranches1(d*0.5, w*(0.2~0.1), d*(0.35~0.03) + 0.3)]
            %(90~10)[$(95~5)lateralBranches1(d*0.5, w*(0.2~0.1), d*(0.35~0.03) + 0.3)]
            %(90~10)[$(95~5)lateralBranches1(d*0.5, w*(0.2~0.1), d*(0.35~0.03) + 0.3)]
            ^(l, w)
            mainBranch(d-1, w - alpha, alpha, l*0.9)
        ;
    ;
    lateralBranches1(d, w, l):
        if d > 0:
            ^(l*0.8, w)lateralBranches(d, w, l)
        ;
    ;
    lateralBranches(d, w, l):
        if d > 0:
            if d < 2: [^(l, w)lateralBranches(d-1, w*0.55, l*(0.65~0.12))];
            %(120~(d*3))[$((30*(1.1 - w))~10)^(l, w)lateralBranches(d-1, w*(0.55~0.1), l*(0.65~0.12))]
            %(120~(d*3))[$((30*(1.1 - w))~10)^(l, w)lateralBranches(d-1, w*(0.55~0.1), l*(0.65~0.12))]
            %(120~(d*3))[$((30*(1.1 - w))~10)^(l, w)lateralBranches(d-1, w*(0.55~0.1), l*(0.65~0.12))]
        else:
            '(0, 0.8~0.2)
            leaf(1, 0.8, 0.65~0.2)
        ;
    ;
    leaf(d, m, w):
        if d > 0:
            %(120)[$(90)'(m, w~(w*0.2))leaf(d-1, m*0.7, w*(0.8~0.1))]
            %(120)[$(90)'(m, w~(w*0.2))leaf(d-1, m*0.7, w*(0.8~0.1))]
            %(120)[$(90)'(m, w~(w*0.2))leaf(d-1, m*0.7, w*(0.8~0.1))]
        ;
    ;
"""

simpleTree = """
    loose: False
    init(5, 5)
    init(d, l):
        ^(0.1, 1)^(2.5*l, 1 - 1/(d+1))mainBranch(d, 1 - 2/(d+2), 1/(d+2), l)
    ;
    mainBranch(d, w, alpha, l):
        if d > 0:
            %(0~180)
            [$(70~5)lateralBranches1(d*0.5, w*(0.2~0.1), d + 0.3)]
            ^(l, w)
            mainBranch(d-1, w - alpha, alpha, l*0.9)
        ;
    ;
    lateralBranches1(d, w, l):
        if d > 0:
            ^(l*1.4, w)lateralBranches(d, w, l*0.6)
        ;
    ;
    lateralBranches(d, w, l):
        if d > 0:
            if d < 5: [^(l, w)lateralBranches(d-1, w*0.55, l*(0.65~0.05))];
            %(120~(d*3))[$((45*(1.1 - w))~10)^(l, w)lateralBranches(d-1, w*(0.55~0.05), l*(0.65~0.12))]
            %(120~(d*3))[$((45*(1.1 - w))~10)^(l, w)lateralBranches(d-1, w*(0.55~0.05), l*(0.65~0.12))]
            %(120~(d*3))[$((45*(1.1 - w))~10)^(l, w)lateralBranches(d-1, w*(0.55~0.05), l*(0.65~0.12))]
        else:
            '(0, 1~0.2)
            leaf(1, 0.8, 0.8~0.2)
        ;
    ;
    leaf(d, m, w):
        if d > 0:
            %(120)[$(90)'(m, w~(w*0.2))leaf(d-1, m*0.7, w*(0.8~0.1))]
            %(120)[$(90)'(m, w~(w*0.2))leaf(d-1, m*0.7, w*(0.8~0.1))]
            %(120)[$(90)'(m, w~(w*0.2))leaf(d-1, m*0.7, w*(0.8~0.1))]
        ;
    ;
"""

s = LSystem(bushWithLeaves)
s.exec()


# Save and re-open the file to clean up the data blocks
bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)
bpy.ops.wm.open_mainfile(filepath=bpy.data.filepath)