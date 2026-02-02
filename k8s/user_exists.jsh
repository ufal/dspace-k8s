    var p=new java.util.Properties();
    try(var i=java.nio.file.Files.newInputStream(java.nio.file.Paths.get("/dspace/config/local.cfg"))){
      p.load(i);
    }
    var url=p.getProperty("db.url");
    var user="dspace";
    var pwd=System.getenv("POSTGRES_PASSWORD");
    var adminEmail=System.getenv("DSPACE_ADMIN_EMAIL");
    try(var con=java.sql.DriverManager.getConnection(url,user,pwd);
        var stmt=con.prepareStatement("SELECT 1 FROM eperson where email=?")){
        stmt.setString(1,adminEmail);
        try(var rs=stmt.executeQuery()){
          if(rs.next()){
            System.out.println("Admin user already exists.");
          }
      }
    };
    /exit
