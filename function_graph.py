#!/usr/bin/env python3
import sys
import pprint
import os
import collections
import argparse

from solidity_parser import parser

class Contract():
    def __init__(self, name):
        self.name = name
        self.base_contracts = []
        self.state_user_vars = {}
        self.usingfors = {}
        self.functions = {}
        self.linearization = []

class Function():
    def __init__(self, name, params, fun_type="Function"):
        self.name = name
        self.params = params
        self.type = fun_type;

    def __eq__(self, other):
        return (self.name == other.name and
                self.params == other.params and
                self.type == other.type)

    def __hash__(self):
        return hash(self.name)

    def __repr__(self):
        return f"{self.name}:{self.params}"

class Call():
    def __init__(self, contract, function, context=None):
        self.contract = contract
        self.function = function
        self.context = context or contract

    def __eq__(self, other):
        return self.contract == other.contract and self.function == other.function

    def __hash__(self):
        return hash(self.function)

    def __repr__(self):
        return f"{self.contract}:{self.function}"

class Edge():
    def __init__(self, caller, callee):
        self.caller = caller
        self.callee = callee

    def __eq__(self, other):
        return self.caller == other.caller and self.callee == other.callee

    def __hash__(self):
        return hash(self.callee)

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

def parse_contracts(path):
    contracts = {}
    to_parse = [path]
    for path in to_parse:
        source = parser.parse_file(path)
        for directive in source["children"]:
            if directive["type"] == "ImportDirective":
                new_path = directive["path"]
                if new_path[0] == '.':
                    new_path = os.path.join(os.path.dirname(path), new_path)
                if (os.path.isfile(new_path) and
                    not any(os.path.samefile(new_path, p) for p in to_parse)):
                    to_parse.append(new_path)
            # Parse contracts
            # Libraries and interfaces are contracts
            if directive["type"] == "ContractDefinition":
                name = directive["name"]
                contracts[name] = Contract(name)
                # Solidity does c3 linearization in reverse (Right to Left)
                bases = [b["baseName"]["namePath"] for b in reversed(directive["baseContracts"])]
                contracts[name].base_contracts = bases
                for node in directive["subNodes"]:
                    parse_node(contracts[name], node)
    for name, contract in contracts.items():
        contract.linearization = c3(name, contracts)
    return contracts

def c3(contract, contracts):
    if contract not in contracts or not contracts[contract].base_contracts:
        return [contract]
    bases = contracts[contract].base_contracts
    return [contract] + c3_merge([c3(base, contracts) for base in bases] + [bases])

def c3_merge(lst):
    if not any(lst):
        return []
    for candidate, *_ in lst:
        if all(candidate not in tail for _, *tail in lst):
            rec = [tail if head == candidate else [head, *tail] for head, *tail in lst]
            return [candidate] + c3_merge([l for l in rec if l])
    else:
        raise TypeError("Couldn't linearize contract")

def parse_node(contract, node):
    if node["type"] == "UsingForDeclaration":
        typename = node["typeName"]
        if typename["type"] == "UserDefinedTypeName":
            contract.usingfors[typename["namePath"]] = node["libraryName"]
    if node["type"] == "StateVariableDeclaration":
        for var in node["variables"]:
            typename = var["typeName"]
            if typename["type"] == "UserDefinedTypeName":
                contract.state_user_vars[var["name"]] = typename["namePath"]
    if node["type"] in ["FunctionDefinition", "ModifierDefinition", "EventDefinition"]:
        name = node["name"]
        calls = []
        fun_type = node["type"][:-len("Definition")]
        if fun_type == "Function":
            if node["isConstructor"]:
                name = contract.name
            calls = [Call("this", Function(x["name"], len(x["arguments"]), "Modifier"))
                     for x in node["modifiers"]]
        if "body" in node and node["body"]:
            get_calls_from_dict(node["body"], calls)
        function = Function(name, len(node["parameters"]["parameters"]), fun_type)
        # FIXME: We cannot do this without compilation
        if function in contract.functions:
            eprint(f"WARNING: Overloaded function {function.name} detected")
            eprint(f"This parser can only differentiate them by the amount of parameters")
            eprint(f"We don't know which function to call, we are likely guessing wrong")
        contract.functions[function] = calls

def get_calls_from_dict(dictionary, function_calls, keyname=""):
    for k, v in dictionary.items():
        if k == "type" and v == "FunctionCall":
            exp = dictionary["expression"]
            param_count = len(dictionary["arguments"])
            fun_type = "Event" if keyname == "eventCall" else "Function"
            if exp["type"] == "Identifier":
                function_calls.append(Call("this", Function(exp["name"], param_count, fun_type)))
            if exp["type"] == "MemberAccess":
                if "name" in exp["expression"]:
                    function = Function(exp["memberName"], param_count, fun_type)
                    function_calls.append(Call(exp["expression"]["name"], function))
        if type(v) is parser.Node:
            get_calls_from_dict(v, function_calls, k)
        if type(v) is list:
            for x in v:
                if type(x) is parser.Node:
                    get_calls_from_dict(x, function_calls)

def walk_call(entry, ignore_list):
    calls = [entry]
    nodes = collections.defaultdict(set)
    edges = collections.defaultdict(set)
    nodes[entry.contract].add(entry)
    for callee in calls:
        nodes[callee.contract].add(callee)
        if callee in ignore_list:
            continue
        for call in contracts[callee.contract].functions[callee.function]:
            search_function = lambda contract : call.function in contract.functions
            contract_to = callee.contract
            context_to = callee.context
            if call.contract in ["this", "super"]:
                # Internal call
                if call.contract == "super":
                    # Search for the same function strictly in base contracts
                    search_function = (lambda contract :
                        not contract is contracts[callee.contract] and
                        call.function in contract.functions)
                    contract_to = find_base(callee.contract, search_function)
                else:
                    contract_to = find_base(callee.context, search_function)
                # We wishfully ignore functions we don't know (Like requires)
                if contract_to is None:
                    continue
            else:
                # Maybe it's a direct call to a contract base (i.e. ContractBase.foo())
                base_name = None
                if call.contract in contracts[callee.contract].linearization:
                    base_name = find_base(call.contract, search_function)
                contract_to = base_name
                if base_name is None:
                    # External call
                    # Search for the base contract holding the state variable
                    search_var = lambda contract : call.contract in contract.state_user_vars
                    base_name = find_base(callee.contract, search_var)
                    # We may not have the contract that holds the state variable
                    if base_name is None:
                        continue
                    contract_to = contracts[base_name].state_user_vars[call.contract]
                    context_to = base_name
                    if contract_to not in contracts:
                        # Maybe it's a struct used with a library, not a contract
                        search_using = lambda contract : contract_to in contract.usingfors
                        base_usingfor = find_base(base_name, search_using)
                        contract_to = contracts[base_usingfor].usingfors[contract_to]
                        lib_function = Function(call.function.name, call.function.params + 1)
                        search_function = lambda contract : lib_function in contract.functions
            call_to = Call(contract_to, call.function, context_to)
            edges[callee.contract].add(Edge(callee, call_to))
            if not call_to in calls: 
                calls.append(call_to)
    return (nodes, edges)


def find_base(contract_name, predicate):
    for base_name in contracts[contract_name].linearization:
        if base_name in contracts and predicate(contracts[base_name]):
            return base_name
    return None

def print_digraph(nodes, edges, ignore_list):
    print("digraph function_graph {")
    print("concentrate = true;")
    print("newrank = true;")
    print("overlap = false;")
    print("edge [color=red];")
    for contract, calls in nodes.items():
        print(f"subgraph cluster_{contract} {{")
        print(f"    label = {contract};")
        print(f"    color = blue;")
        print(f"    edge [color=black]")

        for call in calls:
            shapes = {"Function": "box", "Modifier": "house", "Event": "oval"}
            name = call.function.name or "\"Fallback Function\""
            style = "dashed" if call in ignore_list else "solid";
            print(f"    \"{call}\" [label={name}, style={style}, shape={shapes[call.function.type]}]")
        for edge in edges[contract]:
            if edge.callee.contract == contract:
                print(f"    \"{edge.caller}\" -> \"{edge.callee}\"")
        print("}")

    for contract, edges in edges.items():
        for edge in edges:
            if edge.callee.contract != contract:
                if edge.caller.context == edge.callee.context:
                    print(f"    \"{edge.caller}\" -> \"{edge.callee}\" [color=black]")
                else:                
                    print(f"    \"{edge.caller}\" -> \"{edge.callee}\"")

    print("}")

if __name__ == "__main__":
    if len(sys.argv) == 1 or (len(sys.argv) == 2 and sys.argv[1] in ["-h", "--help"]):
        eprint("Usage: ./function_graph.py FILE [Contract] [Function] [Parameter Count] [--ignore Contract:Function:Count ...]\n")
        eprint("Examples:")
        eprint("Calling the fallback function of contract Test in Test.sol:")
        eprint("./function_graph.py Test.sol\n")
        eprint("Calling the fallback function of contract Token in Test.sol")
        eprint("./function_graph.py Test.sol Token\n")
        eprint("Calling the constructor of contract Token in Test.sol")
        eprint("./function_graph.py Test.sol Token Token\n")
        eprint("Calling the function approve of contract Token in Test.sol")
        eprint("./function_graph.py Test.sol Token approve\n")
        eprint("Calling the function approve that has 3 parameters of contract Token in Test.sol")
        eprint("./function_graph.py Test.sol Token approve 3\n")
        eprint("Calling the function approve that has 3 parameters of contract Token in Test.sol")
        eprint("While also ignoring the function irrelevant with 2 parameters")
        eprint("./function_graph.py Test.sol Token approve 3 --ignore Token:irrelevant:2\n")
        sys.exit(-1)
    prs = argparse.ArgumentParser(add_help=False)
    prs.add_argument('file')
    prs.add_argument('contract', nargs='?')
    prs.add_argument('function', nargs='?', default='')
    prs.add_argument('count', nargs='?', type=int)
    prs.add_argument('--ignore', nargs='+')
    args = prs.parse_args(sys.argv[1:])
    # Walk the function calls
    ignore_list = []
    if args.ignore:
        for ignore in args.ignore:
            call = ignore.split(':')
            if len(call) != 3:
                sys.exit(f"Bad call count {ignore}")
            ignore_list.append(Call(call[0], Function(call[1], int(call[2]))))
    if not os.path.isfile(args.file):
        sys.exit(f"Couldn't find file {args.file}")
    file_param = os.path.realpath(args.file)
    filename = os.path.basename(file_param)
    # Get into contract directory
    os.chdir(os.path.dirname(file_param))
    contracts = parse_contracts(filename)
    # Parse and validate parameters
    contract_name = filename.split(".")[0] if args.contract is None  else args.contract
    if not contract_name in contracts:
        sys.exit(f"Couldn't find contract {contract_name}")
    # Look for matching functions
    FunctionBase = collections.namedtuple("FunctionBase", "fun base")
    bases = contracts[contract_name].linearization
    functions = (FunctionBase(f, b) for b in bases if b in contracts for f in contracts[b].functions)
    functions = [f for f in functions if f.fun.name == args.function and f.fun.type == "Function"]
    if not functions:
        sys.exit(f"Couldn't find function {args.function}")
    if not args.count is None:
        functions = [f for f in functions if f.fun.params == args.count]
        if not functions:
            sys.exit(f"Couldn't find function {args.function} with {args.count} parameters")
    if len(functions) > 1 and functions[0].base == functions[1].base:
        eprint(f"WARNING: Found more than one matching function {args.function}")
        eprint(f"This script cannot differentiate between two functions")
        eprint(f"with the same name and amount of parameters but different types")
        eprint(f"since that requires more compiler information that we do not have")
    (nodes, edges) = walk_call(Call(contract_name, functions[0].fun), ignore_list)
    # Print the resulting graph
    print_digraph(nodes, edges, ignore_list)
