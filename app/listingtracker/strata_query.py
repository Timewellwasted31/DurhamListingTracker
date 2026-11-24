"""The listings query strata.ca's building page sends to its data service, word for word, so the
reader's requests are the page's own (see buildingsales). Recorded from the page on Nov 24, 2026."""

SEARCH_LISTINGS_QUERY = """query SEARCH_LISTINGS_QUERY($filter: ListingFilter) {
  listingsNew(filter: $filter) {
    listings {
      neighbourhood {
        uid
        name
      }
      property {
        uid
        urlContext
        buildingTypes
      }
      freehold {
        geoPoint {
          lat
          lon
        }
      }
      freeholdListing {
        aboutThePropertyDescription
        address
        bathroomCount
        bedroomCount
        bedroomPlusCount
        brokerage
        freeholdListingState
        freeholdUnitUid
        listDate
        listPrice
        lotDepth
        lotFront
        mlsNumber
        ownershipPreference
        distributeToInternetPortals
        parkingSpaces
        possession
        soldDate
        soldPrice
        sqftSizeHigh
        sqftSizeLow
        taxes
        taxYear
        type
        uid
        unavailableDate
        previousDaysOnMarket
        unit
        urlContext
        vowListing
        listPrices {
          price
          priceDate
        }
        images {
          url
        }
        openHouseSlots {
          date
          startTime
          endTime
          status
          type
        }
        __typename
      }
      propertyListing {
        aboutThePropertyDescription
        address
        bathroomCount
        bedroomCount
        bedroomPlusCount
        brokerage
        images {
          url
        }
        listDate
        listPrice
        listPrices {
          price
          priceDate
        }
        geoPoint {
          lat
          lon
        }
        costPerSqFoot
        distributeToInternetPortals
        greatValueListing
        hotListing
        maintenanceFees
        mlsNumber
        ownershipPreference
        parkingSpaces
        possession
        propertyListingState
        propertyUnitUid
        soldDate
        soldPrice
        sqftSizeHigh
        taxes
        taxYear
        sqftSizeLow
        uid
        unavailableDate
        previousDaysOnMarket
        unit
        urlContext
        vowListing
        openHouseSlots {
          date
          startTime
          endTime
          status
          type
        }
        __typename
      }
    }
    pagingStats {
      totalCount
    }
  }
}"""
