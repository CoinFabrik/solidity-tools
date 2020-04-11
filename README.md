# Solidity Function Call Graph Parser

This project aims to provide a simple script that parses python solidity code and outputs a DOT call graph for a given function call.

## Prerequisites

You need `python3` and you also need the `solidity_parser` library which you can get using pip like so:
```
pip3 install solidity_parser
```
You don't need to compile the project to use it. In fact you don't need the project to compile at all. Even if you have missing files the parser will still work, although it obviously won't draw the missing nodes if there are any.

## How to use

You may execute the script as follows:
```
python3 function_graph.py FILE [Contract] [Function] [Parameter Count] [--ignore Contract:Function:Count ...]
```
The script will parse the `FILE` provided, creating a graph starting with the contract with the same name unless `[Contract]` is provided. It will create a graph for calling the fallback function unless another `[Function]` is specified, if there are many with the same name you may also specify the `[Parameter Count]`.

You can also disable parsing certain functions by appending the `--ignore` option, which takes a list of specified contract functions.

### Examples

Calling the fallback function of contract `Contract` in `Contract.sol`
```
./function_graph.py Contract.sol
```
Calling the fallback function of contract `Name` in `Contract.sol`
```
./function_graph.py Contract.sol Name
```
Calling the function `bar` of contract `Name` in `Contract.sol`
```
./function_graph.py Contract.sol Name bar
```
Calling the function `bar` with 3 parameters of contract `Name` in `Contract.sol`
```
function_graph.py Contract.sol Name bar 3
```

### Output

The script outputs a graph in dot format. This graph can be drawn most commonly using GraphViz programs. In particular you can use `dot` in linux as follows:

```
dot -Tpng graph.dot > graph.png
```
This obviously assumes you saved the output in a file called `graph.dot`.

## Limitations

- It is not able to know which contracts are actually deployed. If a different contract is deployed for a contract type different functions may be called. For example, when a contract calls transfer on an ERC20 contract interface, we may never know which transfer function is being called.

- It is not able currently to differentiate functions with the same name and amount of parameters. This is because in order to differentiate those we need compiler information. This can probably be fixed in the future by also accepting the Solidity AST output.
