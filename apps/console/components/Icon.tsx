const paths:Record<string,string[]>={
  shield:['M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6l-8-3Z','m8.5 12 2.5 2.5 4.5-5'],
  overview:['M3 3h7v7H3z','M14 3h7v4h-7z','M14 11h7v10h-7z','M3 14h7v7H3z'],
  devices:['M3 4h18v12H3z','M8 21h8','M12 16v5'],
  policy:['M4 6h16','M4 12h16','M4 18h16','M8 3v6','M16 9v6','M10 15v6'],
  events:['m3 12 4-1 3-7 4 16 3-8h4'],
  audit:['M6 3h12v18H6z','M9 7h6','M9 11h6','M9 15h4'],
  settings:['M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8','m9 3-1 3-3 1-2 3 2 3 1 3 3 1 3 2 3-2 3-1 1-3 2-3-2-3-3-1-1-3Z'],
  sun:['M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8','M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5'],
  moon:['M20 15A9 9 0 0 1 9 4a9 9 0 1 0 11 11Z'],
  refresh:['M20 7v5h-5','M4 17v-5h5','M6 7a7 7 0 0 1 12-1l2 3','M18 17a7 7 0 0 1-12 1l-2-3'],
  arrow:['M5 12h14','m14 7 5 5-5 5'],plus:['M12 5v14','M5 12h14'],
  close:['m6 6 12 12','M6 18 18 6'],logout:['M10 4H4v16h6','M10 12h11','m17 8 4 4-4 4'],
  check:['m5 12 4 4L19 6'],alert:['m12 3 10 18H2Z','M12 9v5','M12 17v.1'],download:['M12 3v12','m7 10 5 5 5-5','M4 17v4h16v-4'],
};
export default function Icon({name,size=20}:{name:string;size?:number}){return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{(paths[name]||paths.shield).map((d,i)=><path key={i} d={d}/>)}</svg>;}
