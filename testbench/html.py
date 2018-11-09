HEAD = """
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>TAP Matrix</title>
    <style>
      table {
          border-collapse: collapse;
          table-layout: fixed;
          margin: auto;
      }
      th, td {
          border: solid 1px grey;
      }


      /* Rotate the headers */
      tr:first-child th, tr:first-child td {
          border: none;
      }
      tr:first-child th {
          padding: 0;
          white-space: nowrap;
          vertical-align: bottom;

          /* width: auto is all screwed up because of rotation */
          width: 2em;
          max-width: 2em;
      }
      tr:first-child th > * {
          margin: 0;
          display: inline-block;
          border-bottom: solid 1px grey;

          transform-origin: bottom left;
          transform: rotate(-60deg);
          margin-left: 100%; /* move it 100% of the width to the
                                right, so that the corner that was the
                                bottom-left is now the bottom right */
          pointer-events: none;
      }
      tr:first-child th > * > * {
          pointer-events: auto;
      }
      /* Stretch the element vertically to be a square, this is
       * important for getting the height of the th to be correct */
      tr:first-child th > *::before {
          content: '';
          width: 0;
          padding-top: 100%; /* 100% of the width */
          display: inline-block;
          vertical-align: bottom;
      }

      /* Style the links */
      th a {
          color: inherit;
          text-decoration: inherit;
      }
      th a:hover {
          background-color: #8484ed;
      }

      /* TAP formatting */
      tr:nth-child(2) th, tr:nth-child(2) td {
          border-bottom: solid 3px black;
      }
      td.ok          { background-color: green; }
      td.not_ok      { background-color: red; }
      td.todo_ok     { background-color: purple; }
      td.todo_not_ok { background-color: purple; }
      td.skip        { background-color: red; }
      td.missing     { background-color: red; }

      /* Footer */
      pre {
          margin: auto;
          margin-top: auto;
          border: solid 1px grey;
          margin-top: 2em;
          background-color: #ccccee;
          padding: 0.5em;
      }
    </style>
  </head>
  <body>
"""

TAIL = """
  </body>
</html>
"""
